#!/usr/bin/env python3
"""News Center sender — posts analysed items from data/outbox.json to Telegram.

* Exactly-once: every outbox message has an id; ids already in data/sent.json are skipped.
* Story updates: a message with "reply_to_event" is sent as a reply to the first message
  of that event (thread), if we know its Telegram message_id.
* Live price at send time: the token {{price:SYMBOL}} (Yahoo Finance symbol, e.g.
  BTC-USD, EURUSD=X, GC=F, NVDA, ^GSPC) is replaced with the latest price.
* Respects Telegram flood limits (spacing + retry_after on HTTP 429).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUTBOX = ROOT / "data" / "outbox.json"
SENT = ROOT / "data" / "sent.json"

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"
PRICE_RE = re.compile(r"\{\{price:([A-Za-z0-9=^.\-]+)\}\}")
SPACING_S = 3.2  # stay under ~20 messages/minute per group
MAX_LEN = 4000


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


_price_cache: dict[str, str] = {}


def price(symbol: str) -> str:
    if symbol in _price_cache:
        return _price_cache[symbol]
    out = "نامشخص"
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"interval": "1m", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        meta = r.json()["chart"]["result"][0]["meta"]
        p = meta.get("regularMarketPrice")
        t = meta.get("regularMarketTime")
        if p is not None:
            digits = 5 if p < 10 else (2 if p < 100000 else 0)
            hhmm = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%H:%M") if t else ""
            out = f"{p:,.{digits}f} (Yahoo, {hhmm} UTC)"
    except Exception as ex:  # noqa: BLE001 — price is optional
        print(f"price {symbol} failed: {ex}", file=sys.stderr)
    _price_cache[symbol] = out
    return out


def send(text: str, reply_to: int | None) -> int | None:
    payload = {"chat_id": CHAT_ID, "text": text[:MAX_LEN], "disable_web_page_preview": True}
    if reply_to:
        payload["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
    for attempt in range(5):
        r = requests.post(f"{API}/sendMessage", json=payload, timeout=20)
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code == 429:
            wait = int(data.get("parameters", {}).get("retry_after", 5)) + 1
            print(f"flood limit, waiting {wait}s")
            time.sleep(wait)
            continue
        if data.get("ok"):
            return data["result"]["message_id"]
        print(f"send failed ({r.status_code}): {data.get('description')}", file=sys.stderr)
        if r.status_code in (400, 403):
            return None  # bad chat id / bot not in group / bad text — don't hammer
        time.sleep(3 * (attempt + 1))
    return None


def main() -> int:
    if not TOKEN or not CHAT_ID:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID secrets are not set — nothing sent.")
        return 0
    outbox = load(OUTBOX, {"messages": []}).get("messages", [])
    sent = load(SENT, {"sent": {}, "events": {}, "failed": {}})
    sent.setdefault("failed", {})
    pending = [m for m in outbox
               if m["id"] not in sent["sent"] and int(sent["failed"].get(m["id"], 0)) < 3]
    print(f"{len(pending)} pending of {len(outbox)}")
    failures = 0
    for i, m in enumerate(pending):
        text = PRICE_RE.sub(lambda mo: price(mo.group(1)), m["text"])
        reply_to = sent["events"].get(m.get("reply_to_event") or "")
        mid = send(text, reply_to)
        if mid is None:
            failures += 1
            sent["failed"][m["id"]] = int(sent["failed"].get(m["id"], 0)) + 1
            if failures >= 3:
                print("3 failures in a row — stopping this run")
                break
            continue
        failures = 0
        sent["sent"][m["id"]] = {"message_id": mid, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        if m.get("event_id") and m["event_id"] not in sent["events"]:
            sent["events"][m["event_id"]] = mid
        SENT.write_text(json.dumps(sent, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        if i < len(pending) - 1:
            time.sleep(SPACING_S)
    # prune sent ids older than 7 days
    cutoff = time.time() - 7 * 86400
    sent["sent"] = {k: v for k, v in sent["sent"].items()
                    if datetime.fromisoformat(v["at"]).timestamp() > cutoff}
    SENT.write_text(json.dumps(sent, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
