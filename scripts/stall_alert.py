#!/usr/bin/env python3
"""Runs after every collect. If the AI analyst has not completed a round for > 20 min while new items
are waiting, send ONE Telegram alert (and one recovery notice later). Keeps the user informed if analysis stops."""
import json, os, time
from datetime import datetime
from pathlib import Path
import requests

D = Path("data"); ALERT = D / "alert.json"
tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
if not tok or not chat:
    raise SystemExit(0)
ts = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else 0
cur = json.loads((D / "cursor.json").read_text())
seq = json.loads((D / "seen.json").read_text()).get("seq", 0)
waiting = seq - cur.get("last_seq", 0)
idle = (time.time() - ts(cur.get("last_round_at") or cur.get("updated"))) / 60
state = json.loads(ALERT.read_text()) if ALERT.exists() else {"alerted": False}
def say(t):
    requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": chat, "text": t}, timeout=15)
if idle > 20 and waiting > 0 and not state["alerted"]:
    say(f"⛔️ هشدار سیستم: تحلیل خبرها {int(idle)} دقیقه است متوقف شده و {waiting} خبر در صف مانده. "
        "خبرها جمع‌آوری می‌شوند ولی تحلیل/ارسال نمی‌شوند. لطفاً در چت Claude بنویس «ادامه بده».")
    state = {"alerted": True, "at": time.time()}
elif idle <= 20 and state["alerted"]:
    say("✅ تحلیل خبرها دوباره فعال شد.")
    state = {"alerted": False}
ALERT.write_text(json.dumps(state))
