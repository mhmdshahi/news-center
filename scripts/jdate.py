#!/usr/bin/env python3
"""Print Tehran time + Jalali date for a UTC timestamp:  jdate.py 2026-11-25T17:23:00Z  ->  ۴ آذر : ساعت ۲۰:۵۳ به وقت تهران"""
import sys
from datetime import datetime, timedelta, timezone

MONTHS = ["فروردین","اردیبهشت","خرداد","تیر","مرداد","شهریور","مهر","آبان","آذر","دی","بهمن","اسفند"]
DIG = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

def g2j(gy, gm, gd):
    g_d_m = [0,31,59,90,120,151,181,212,243,273,304,334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365*gy + (gy2+3)//4 - (gy2+99)//100 + (gy2+399)//400 + gd + g_d_m[gm-1]
    jy = -1595 + 33*(days//12053); days %= 12053
    jy += 4*(days//1461); days %= 1461
    if days > 365:
        jy += (days-1)//365; days = (days-1) % 365
    jm, jd = (1 + days//31, 1 + days % 31) if days < 186 else (7 + (days-186)//30, 1 + (days-186) % 30)
    return jy, jm, jd

def fmt(ts_utc: datetime) -> str:
    t = ts_utc.astimezone(timezone(timedelta(hours=3, minutes=30)))  # Iran: UTC+3:30, no DST
    _, jm, jd = g2j(t.year, t.month, t.day)
    return f"{jd} {MONTHS[jm-1]} : ساعت {t:%H:%M} به وقت تهران".translate(DIG)

if __name__ == "__main__":
    s = sys.argv[1] if len(sys.argv) > 1 else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(fmt(datetime.fromisoformat(s.replace("Z", "+00:00"))))
