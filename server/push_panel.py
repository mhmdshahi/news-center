#!/usr/bin/env python3
"""Push new cards from data/outbox.json to the Radarban panel (radarban.com/panel) — exactly once.

* Resolves the live tokens at push time, like the Telegram sender: {{price:SYM@ts}} (price at news
  time vs now, from Yahoo), {{age:ts}}, [[name|url]] → clickable source name.
* Signs every request: X-Radar-Ts (unix seconds) + X-Radar-Sig = hex HMAC-SHA256(secret, "<ts>.<body>").
  The panel rejects unsigned / old (> 5 min) requests.
* Delivery log: data/panel_sent.json  {"sent": {msg_id: {"at": iso}}, "failed": {msg_id: n}}

Env: PANEL_URL (e.g. https://radarban.com/panel), PANEL_SECRET (same value as INGEST_SECRET in the
panel's config.php), RADAR_DATA (data dir).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sender"))
import send as S  # noqa: E402  (price / age / link helpers)

DATA = S.DATA
LOG = DATA / "panel_sent.json"
PANEL_URL = os.environ.get("PANEL_URL", "").rstrip("/")
SECRET = os.environ.get("PANEL_SECRET", "")
BATCH = 20


def post(items: list[dict]) -> dict:
    """POST a batch to the panel. Raises on any failure."""
    if not PANEL_URL or not SECRET:
        raise RuntimeError("PANEL_URL / PANEL_SECRET not set")
    body = json.dumps({"items": items}, ensure_ascii=False).encode("utf-8")
    ts = str(int(time.time()))
    sig = hmac.new(SECRET.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    last = None
    for attempt in range(3):
        try:
            r = requests.post(f"{PANEL_URL}/api/ingest.php", data=body, timeout=30,
                              headers={"Content-Type": "application/json; charset=utf-8",
                                       "X-Radar-Ts": ts, "X-Radar-Sig": sig,
                                       "User-Agent": "RadarbanPusher/1.0"})
            data = r.json()
            if r.status_code == 200 and data.get("ok"):
                return data
            last = f"HTTP {r.status_code}: {data}"
            if r.status_code in (400, 401, 403):
                break  # bad secret / bad payload — retrying won't help
        except (requests.RequestException, ValueError) as ex:
            last = str(ex)
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"panel push failed: {last}")


def render(m: dict) -> dict:
    text = S.PRICE_RE.sub(lambda mo: S.price(mo.group(1), mo.group(2)), m["text"])
    text = S.AGE_RE.sub(lambda mo: S.age(mo.group(1)), text)
    sig = m.get("signal") or {}
    line = m.get("line")
    if not line:  # cards written before line/title were stored
        mo = re.search(r"لاین خبری ([^\n]+)", m["text"])
        line = mo.group(1).strip() if mo else None
    title = m.get("title")
    if not title:
        mo = re.search(r"تیتر خبر : ([^\n]+)", m["text"])
        title = mo.group(1).strip() if mo else None
    return {
        "msg_id": m["id"], "kind": "card", "code": m.get("code"), "event_id": m.get("event_id"),
        "reply_to_event": m.get("reply_to_event"), "created": m.get("created"),
        "importance": m.get("importance"), "line": line, "title": title,
        "sig_dir": sig.get("dir"), "sig_label": sig.get("label"), "sig_symbol": sig.get("symbol"),
        "sig_conf": sig.get("conf"), "html": S.to_html(text),
    }


def main() -> int:
    if not PANEL_URL or not SECRET:
        print("PANEL_URL / PANEL_SECRET not set — nothing pushed")
        return 0
    outbox = S.load(DATA / "outbox.json", {"messages": []}).get("messages", [])
    log = S.load(LOG, {"sent": {}, "failed": {}})
    log.setdefault("failed", {})
    pending = [m for m in outbox if "code" in m and m["id"] not in log["sent"]
               and int(log["failed"].get(m["id"], 0)) < 5]
    if not pending:
        return 0
    print(f"{len(pending)} pending")
    for i in range(0, len(pending), BATCH):
        chunk = pending[i:i + BATCH]
        try:
            post([render(m) for m in chunk])
        except Exception as ex:  # noqa: BLE001
            print(ex, file=sys.stderr)
            for m in chunk:
                log["failed"][m["id"]] = int(log["failed"].get(m["id"], 0)) + 1
            break
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for m in chunk:
            log["sent"][m["id"]] = {"at": now}
            log["failed"].pop(m["id"], None)
    cutoff = time.time() - 7 * 86400
    log["sent"] = {k: v for k, v in log["sent"].items() if datetime.fromisoformat(v["at"]).timestamp() > cutoff}
    tmp = LOG.with_name(LOG.name + ".tmp")
    tmp.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, LOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
