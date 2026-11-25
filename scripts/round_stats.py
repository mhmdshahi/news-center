#!/usr/bin/env python3
"""Round report for the analyst. Usage: round_stats.py <since ISO UTC> <analysed> <sent>
Sums every collector run since the previous analyst round."""
import json, sys
from pathlib import Path
since, analysed, sent = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
runs = [json.loads(l) for l in Path("data/runs.jsonl").read_text().splitlines() if l.strip()]
runs = [r for r in runs if r["at"] > since] or runs[-1:]
last = runs[-1]
fa = sorted({f for r in runs for f in r.get("failed", [])})
fa_txt = ("، خطا: " + "، ".join(fa)) if fa else ""
D = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
print((f"منابع چک‌شده: {last['sources_ok']}/{last['sources_total']} ({len(runs)} بار جمع‌آوری{fa_txt}) | "
       f"خبر دریافت‌شده: {sum(r['entries'] for r in runs)} آیتم ({sum(r.get('unseen', 0) for r in runs)} تازه) | "
       f"Library بعد از فیلتر: {sum(r['new'] for r in runs)} | "
       f"آنالیز و ارسال: {analysed} آنالیز، {sent} ارسال").translate(D))
