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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from jdate import g2j, MONTHS, DIG  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUTBOX = ROOT / "data" / "outbox.json"
SENT = ROOT / "data" / "sent.json"

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"
PRICE_RE = re.compile(r"\{\{price:([A-Za-z0-9=^.\-]+)(?:@([0-9T:\-]+Z))?\}\}")
AGE_RE = re.compile(r"\{\{age:([0-9T:\-]+Z)\}\}")
SPACING_S = 3.2  # stay under ~20 messages/minute per group
MAX_LEN = 4000


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default



TEHRAN = timezone(timedelta(hours=3, minutes=30))


def teh(ts: float) -> str:
    """Unix ts -> «۴ آذر ساعت ۲۲:۳۶ تهران» (Jalali date, Tehran time, Persian digits)."""
    t = datetime.fromtimestamp(ts, tz=TEHRAN)
    _, jm, jd = g2j(t.year, t.month, t.day)
    return f"{jd} {MONTHS[jm - 1]} ساعت {t:%H:%M} تهران".translate(DIG)


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


MOVE_WARN = {"fx": 0.25, "crypto": 1.5, "default": 1.0}  # % move since news that means "already priced in"


def _klass(symbol: str) -> str:
    if symbol.endswith("=X") or symbol == "DX-Y.NYB":
        return "fx"
    if symbol.endswith("-USD"):
        return "crypto"
    return "default"


def _chart(symbol: str):
    for attempt in range(3):
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={"interval": "1m", "range": "1d", "includePrePost": "true"},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
            res = r.json()["chart"]["result"][0]
            pts = [(t, c) for t, c in zip(res.get("timestamp") or [], res["indicators"]["quote"][0]["close"])
                   if c is not None]
            return res["meta"], pts
        except Exception as ex:  # noqa: BLE001
            print(f"price {symbol} attempt {attempt + 1} failed: {ex}", file=sys.stderr)
            time.sleep(2)
    return None, []


def price(symbol: str, at_iso: str | None = None) -> str:
    """Accurate Yahoo price. With at_iso: price at news time AND now, plus the move in between
    (warns when most of the move has probably already happened). Old data is always labelled."""
    key = f"{symbol}@{at_iso}"
    if key in _price_cache:
        return _price_cache[key]
    meta, pts = _chart(symbol)
    if not meta:
        out = "نامشخص (Yahoo در دسترس نبود)"
    else:
        if pts:
            t, p = pts[-1]
        else:
            p, t = meta["regularMarketPrice"], meta["regularMarketTime"]
        d = _decimals(symbol, p)
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        now_txt = f"{p:,.{d}f}" + (f" ({(p / prev - 1) * 100:+.2f}% روزانه)" if prev else "")
        hhmm = teh(t)
        stale = (time.time() - t) / 60 > STALE_MIN
        out = None
        if at_iso and pts:
            at = datetime.fromisoformat(at_iso.replace("Z", "+00:00")).timestamp()
            before = [(tt, c) for tt, c in pts if tt <= at]
            if before and at - before[-1][0] < 30 * 60:
                t0, p0 = before[-1]
                move = (p / p0 - 1) * 100
                out = (f"{p0:,.{d}f} (زمان خبر، {teh(t0)}) ← هنگام ارسال {now_txt} ({hhmm})؛ "
                       f"حرکت از زمان خبر: {move:+.2f}%")
                if abs(move) >= MOVE_WARN[_klass(symbol)]:
                    out += " ⚠️ بخش زیادی از حرکت احتمالاً انجام شده"
        if out is None:
            out = f"{now_txt} (هنگام ارسال، {hhmm})"
        if stale:
            out += " — بازار بسته یا کم‌معامله؛ آخرین معامله " + hhmm
    _price_cache[key] = out
    return out


def age(published_iso: str) -> str:
    mins = (time.time() - datetime.fromisoformat(published_iso.replace("Z", "+00:00")).timestamp()) / 60
    n = max(0, round(mins))
    txt = f"ارسال {n} دقیقه بعد از انتشار".translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    return txt + (" ⚠️ خبر کهنه است" if n > 30 else "")


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
        text = PRICE_RE.sub(lambda mo: price(mo.group(1), mo.group(2)), m["text"])
        text = AGE_RE.sub(lambda mo: age(mo.group(1)), text)
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
