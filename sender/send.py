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
STALE_MIN = 20  # last trade older than this -> say so explicitly


def _decimals(symbol: str, p: float) -> int:
    if symbol.endswith("=X"):
        return 3 if "JPY" in symbol else 5
    if p < 1:
        return 5
    if p < 100:
        return 3 if symbol.endswith("-USD") else 2
    return 2


def price(symbol: str) -> str:
    """Latest Yahoo Finance price from the newest 1-minute candle (incl. pre/post market),
    with its UTC time and % change vs previous close. Old data is labelled, never passed off as live."""
    if symbol in _price_cache:
        return _price_cache[symbol]
    out = "نامشخص (Yahoo در دسترس نبود)"
    for attempt in range(3):
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={"interval": "1m", "range": "1d", "includePrePost": "true"},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
            res = r.json()["chart"]["result"][0]
            meta = res["meta"]
            pts = [(t, c) for t, c in zip(res.get("timestamp") or [], res["indicators"]["quote"][0]["close"])
                   if c is not None]
            if pts:
                t, p = pts[-1]
            else:
                p, t = meta["regularMarketPrice"], meta["regularMarketTime"]
            prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            age_min = (time.time() - t) / 60
            hhmm = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%H:%M")
            txt = f"{p:,.{_decimals(symbol, p)}f}"
            if prev:
                txt += f" ({(p / prev - 1) * 100:+.2f}%)"
            if age_min > STALE_MIN:
                txt += f" — آخرین معامله {hhmm} UTC (بازار بسته یا کم‌معامله)"
            else:
                txt += f" — Yahoo، {hhmm} UTC"
            out = txt
            break
        except Exception as ex:  # noqa: BLE001 — price is optional
            print(f"price {symbol} attempt {attempt + 1} failed: {ex}", file=sys.stderr)
            time.sleep(2)
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


def detect_private_chat() -> str:
    """Fallback when TELEGRAM_CHAT_ID is not set: use the private chat of whoever
    last pressed Start / messaged the bot (Telegram keeps updates ~24 h)."""
    try:
        r = requests.get(f"{API}/getUpdates", params={"limit": 100}, timeout=20).json()
    except Exception:  # noqa: BLE001
        return ""
    for u in reversed(r.get("result", [])):
        chat = (u.get("message") or {}).get("chat") or {}
        if chat.get("type") == "private":
            return str(chat["id"])
    return ""


def main() -> int:
    global CHAT_ID
    if not TOKEN:
        print("TELEGRAM_BOT_TOKEN secret is not set — nothing sent.")
        return 0
    if not CHAT_ID:
        CHAT_ID = detect_private_chat()
        if not CHAT_ID:
            print("TELEGRAM_CHAT_ID not set and nobody has pressed Start in the bot — nothing sent.")
            return 0
        print("using private chat detected from /start (id hidden)")
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
