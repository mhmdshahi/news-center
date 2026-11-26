#!/usr/bin/env python3
"""«برایند ۶ ساعته» — scores every directional signal sent in the last N hours.

For each outbox message with signal.dir up/down (neutral / no-signal cards are ignored):
price at news time (Yahoo) vs price now → % move in the signal's direction (+ = the signal
would have made money, − = lost). Sends one Telegram message with the per-code list and the
overall result (equal-weight average), excluding fees/spread.

Usage: python scripts/review.py [--hours 6] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sender"))
sys.path.insert(0, str(ROOT / "scripts"))
import send as S  # noqa: E402  (reuses Telegram + Tehran-time helpers)

FA = str.maketrans("0123456789.-+", "۰۱۲۳۴۵۶۷۸۹٫−+")
DIRS = {"up": "صعودی", "down": "نزولی"}


def fa(x: str) -> str:
    return x.translate(FA)


def chart(symbol: str):
    for attempt in range(3):
        try:
            r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                             params={"interval": "5m", "range": "5d", "includePrePost": "true"},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            res = r.json()["chart"]["result"][0]
            return [(t, c) for t, c in zip(res.get("timestamp") or [], res["indicators"]["quote"][0]["close"])
                    if c is not None]
        except Exception as ex:  # noqa: BLE001
            print(f"chart {symbol} attempt {attempt + 1}: {ex}", file=sys.stderr)
            time.sleep(2)
    return []


def entry_price(pts, at: float):
    """Price a trader could actually have had: last trade ≤ news time if recent (≤30 min),
    otherwise the first trade after the news (market was closed)."""
    before = [(t, c) for t, c in pts if t <= at]
    if before and at - before[-1][0] <= 30 * 60:
        return before[-1]
    after = [(t, c) for t, c in pts if t > at]
    return after[0] if after else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    outbox = json.loads((S.DATA / "outbox.json").read_text(encoding="utf-8")).get("messages", [])
    sent = {}
    for name in ("sent.json", "panel_sent.json"):  # Telegram and/or panel delivery times
        try:
            for k, v in json.loads((S.DATA / name).read_text(encoding="utf-8")).get("sent", {}).items():
                sent.setdefault(k, v)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    now = time.time()
    since = now - a.hours * 3600

    rows, cache = [], {}
    for m in outbox:
        sig = m.get("signal") or {}
        if sig.get("dir") not in DIRS or "code" not in m or m["id"] not in sent:
            continue
        t_sent = datetime.fromisoformat(sent[m["id"]]["at"]).timestamp()
        if t_sent < since:
            continue
        sym = sig["symbol"]
        if sym not in cache:
            cache[sym] = chart(sym)
        pts = cache[sym]
        at = datetime.fromisoformat(sig["at"].replace("Z", "+00:00")).timestamp()
        e = entry_price(pts, at) if pts else None
        row = {"code": m["code"], "label": sig["label"], "dir": sig["dir"], "conf": sig.get("conf", ""),
               "sym": sym}
        if not pts or not e:
            row["status"] = "pending"
        else:
            p0 = e[1]
            t1, p1 = pts[-1]
            ret = (p1 / p0 - 1) * 100 * (1 if sig["dir"] == "up" else -1)
            row.update(status="ok", p0=p0, p1=p1, ret=ret, d=S._decimals(sym, p1),
                       closed=(now - t1) > 20 * 60)
        rows.append(row)

    t_from = S.teh(since)
    t_to = S.teh(now)
    lines = ["📊 برایند ۶ ساعته", f"بازه: {t_from} تا {t_to}", ""]
    scored = [r for r in rows if r["status"] == "ok"]
    if not rows:
        lines.append("در این ۶ ساعت هیچ خبر سیگنال‌دار (صعودی/نزولی) ارسال نشد؛ خبرهای خنثی و بدون سیگنال بررسی نمی‌شوند.")
    for r in sorted(rows, key=lambda r: r["code"]):
        head = f"کد {fa(str(r['code']))} | {r['label']} = {DIRS[r['dir']]} (اطمینان: {r['conf']})"
        if r["status"] != "ok":
            lines += [head, "قیمت در دسترس نیست یا بازار هنوز بعد از خبر معامله نشده — محاسبه نشد", ""]
            continue
        d = r["d"]
        mark = "✅ سود" if r["ret"] > 0.005 else ("❌ ضرر" if r["ret"] < -0.005 else "➖ بی‌تغییر")
        extra = " (بازار بسته؛ آخرین معامله)" if r["closed"] else ""
        p0s = fa(format(r["p0"], f",.{d}f"))
        p1s = fa(format(r["p1"], f",.{d}f"))
        rets = fa(format(r["ret"], "+.2f"))
        lines += [head, f"{p0s} ← {p1s}{extra} = \u200e{rets}٪ {mark}", ""]
    if scored:
        wins = sum(r["ret"] > 0.005 for r in scored)
        losses = sum(r["ret"] < -0.005 for r in scored)
        flat = len(scored) - wins - losses
        avg = sum(r["ret"] for r in scored) / len(scored)
        total = sum(r["ret"] for r in scored)
        verdict = "✅ در سود" if avg > 0.005 else ("❌ در ضرر" if avg < -0.005 else "➖ سربه‌سر")
        lines += ["── جمع‌بندی ──",
                  f"سیگنال‌های بررسی‌شده: {fa(str(len(scored)))} | سود: {fa(str(wins))} | ضرر: {fa(str(losses))} | بی‌تغییر: {fa(str(flat))}",
                  f"درصد کلی (میانگین هم‌وزن همهٔ سیگنال‌ها): \u200e{fa(format(avg, '+.2f'))}٪ — {verdict}",
                  f"جمع ساده‌ی درصدها: \u200e{fa(format(total, '+.2f'))}٪",
                  "",
                  "محاسبهٔ فرضی است: ورود با قیمت زمان خبر (یا اولین معامله بعد از آن اگر بازار بسته بود)، "
                  "بدون کارمزد، اسپرد و اهرم؛ عملکرد گذشته تضمین آینده نیست."]
    text = "\n".join(lines).strip()
    print(text)
    if a.dry_run:
        return 0
    if os.environ.get("PANEL_URL"):
        sys.path.insert(0, str(ROOT / "server"))
        import push_panel  # noqa: E402
        push_panel.post([{"msg_id": "r-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M"), "kind": "review",
                          "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "html": S.to_html(text)}])
        print("review posted to panel")
    if not S.TOKEN:
        print("no TELEGRAM_BOT_TOKEN — not sent")
        return 0
    if not S.CHAT_ID:
        S.CHAT_ID = S.detect_private_chat()
    S.send(text, None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
