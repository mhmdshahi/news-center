#!/usr/bin/env python3
"""VPS → panel delivery (route 1 of 2: push). Runs every 30 s from a systemd timer.

1. New cards in data/outbox.json are rendered once (live price at news time vs now, age, clickable
   source names) and queued in data/panel_store.json with an increasing rseq (see server/store.py).
2. Undelivered items are POSTed in order (oldest first, batches of 20) to <PANEL_URL>/api/ingest.php,
   signed: X-Radar-Ts + X-Radar-Sig = hex HMAC-SHA256(secret, "<ts>.<body>").
   With nothing to send, an empty signed heartbeat is posted so the panel knows the link is alive.
3. Nothing is ever dropped: on failure the queue simply waits (route 2, the panel's pull cron via
   server/relay.py, may deliver it meanwhile). Analysis / collection never wait on this process.
4. If delivery has been failing for ≥ ALERT_MIN minutes, one alert (and later one recovery notice)
   goes to the admin's Telegram (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID), if configured.

Env: PANEL_URL, PANEL_SECRET (= ingest_secret in the panel's inc/config.php), RADAR_DATA.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "sender"))
import send as S  # noqa: E402  (price / age / link helpers)
import store  # noqa: E402

PANEL_URL = os.environ.get("PANEL_URL", "").rstrip("/")
SECRET = os.environ.get("PANEL_SECRET", "")
BATCH = 20
TIMEOUT = 20
ALERT_MIN = 10


def sign(payload: bytes, ts: str) -> str:
    return hmac.new(SECRET.encode(), ts.encode() + b"." + payload, hashlib.sha256).hexdigest()


def post(items: list[dict], heartbeat: dict | None = None) -> dict:
    """One signed POST to the panel. Raises on failure (caller keeps the queue)."""
    if not PANEL_URL or not SECRET:
        raise RuntimeError("PANEL_URL / PANEL_SECRET not set")
    body = json.dumps({"items": items, "heartbeat": heartbeat or {}}, ensure_ascii=False).encode("utf-8")
    ts = str(int(time.time()))
    r = requests.post(f"{PANEL_URL}/api/ingest.php", data=body, timeout=TIMEOUT,
                      headers={"Content-Type": "application/json; charset=utf-8", "X-Radar-Ts": ts,
                               "X-Radar-Sig": sign(body, ts), "User-Agent": "RadarbanPusher/1.1"})
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code != 200 or not data.get("ok"):
        raise RuntimeError(f"HTTP {r.status_code}: {str(data or r.text[:200])}")
    return data


def render(m: dict) -> dict:
    text = S.PRICE_RE.sub(lambda mo: S.price(mo.group(1), mo.group(2)), m["text"])
    text = S.AGE_RE.sub(lambda mo: S.age(mo.group(1)), text)
    sig = m.get("signal") or {}
    line = m.get("line")
    if not line:  # cards written before line/title were stored on the message
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


def queue_new_cards() -> int:
    outbox = S.load(S.DATA / "outbox.json", {"messages": []}).get("messages", [])
    known = {it["msg_id"] for it in store.read()["items"]}
    legacy = S.load(S.DATA / "panel_sent.json", {"sent": {}}).get("sent", {})  # marked as already handled
    todo = [m for m in outbox if "code" in m and m["id"] not in known and m["id"] not in legacy]
    if not todo:
        return 0
    rendered = [render(m) for m in todo]  # network (Yahoo) outside the lock
    n = 0
    with store.locked() as st:
        for it in rendered:
            n += store.enqueue(st, it)
    return n


def alert(text: str) -> None:
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not tok or not chat:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": text}, timeout=15)
    except requests.RequestException:
        pass


def check_alert(h: dict) -> None:
    st_path = store.DATA / "panel_alert.json"
    state = S.load(st_path, {"alerted": False})
    since = h.get("fail_since")
    if since and not state["alerted"] and time.time() - store._ts(since) >= ALERT_MIN * 60:
        waiting = sum(1 for it in store.read()["items"] if not it.get("delivered_at"))
        alert(f"⛔️ راداربان: ارسال به پنل از {int((time.time() - store._ts(since)) / 60)} دقیقه پیش قطع است "
              f"({waiting} مورد در صف، چیزی از دست نمی‌رود). آخرین خطا: {h.get('last_error')}")
        state = {"alerted": True}
    elif not since and state["alerted"]:
        alert("✅ راداربان: ارتباط با پنل برقرار شد؛ صف در حال ارسال است.")
        state = {"alerted": False}
    st_path.write_text(json.dumps(state), encoding="utf-8")


def main() -> int:
    if not PANEL_URL or not SECRET:
        print("PANEL_URL / PANEL_SECRET not set — nothing pushed")
        return 0
    added = queue_new_cards()
    if added:
        print(f"queued {added}")
    pending = sorted((it for it in store.read()["items"] if not it.get("delivered_at")), key=lambda x: x["rseq"])
    hb = {"at": store.now_iso(), "queue": len(pending)}
    try:
        if not pending:
            post([], hb)
        for i in range(0, len(pending), BATCH):
            chunk = pending[i:i + BATCH]
            post([store.payload_item(it) for it in chunk], hb)
            with store.locked() as st:
                store.mark_delivered(st, msg_ids={it["msg_id"] for it in chunk}, via="push")
            print(f"delivered {len(chunk)} (up to rseq {chunk[-1]['rseq']})")
        h = store.health_update(True, "push")
    except Exception as ex:  # noqa: BLE001
        print(f"push failed: {ex}", file=sys.stderr)
        h = store.health_update(False, "push", str(ex))
    check_alert(h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
