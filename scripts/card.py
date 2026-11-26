#!/usr/bin/env python3
"""Card builder used by the analyst each round.

* Every message gets a news code (کد خبر) from cursor.json `next_code` (starts at 1000).
* The signal line is explicit: «⚠️ سیگنال : USD/CNH = صعودی ( دلیل ) ( اطمینان: کم )».
* The structured signal is stored on the outbox message so scripts/review.py can score it
  (only «صعودی»/«نزولی» are scored; «خنثی»/«ندارد» are not).
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
DIRS = {"up": "صعودی", "down": "نزولی", "neutral": "خنثی", "none": "ندارد"}
LABELS = [(20, "کم"), (40, "معمولی"), (60, "مهم"), (80, "بسیار مهم"), (100, "حیاتی")]


def _load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


class Round:
    def __init__(self):
        self.inbox = {}
        for line in (DATA / "inbox.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                self.inbox[d["seq"]] = d
        self.out = _load("outbox.json")
        self.ev = _load("events.json")
        self.cur = _load("cursor.json")
        self.cur.setdefault("next_code", 1000)
        self.cur.setdefault("skipped", {})
        self.now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z")
        self.prev_round = self.cur.get("last_round_at")
        self.ids = {e["id"] for e in self.ev["events"]}

    # ── helpers ──────────────────────────────────────────────
    def url(self, seq):
        return self.inbox[seq]["url"].split("?")[0]

    def pub(self, seq):
        return self.inbox[seq]["published"]

    def skip(self, seq, reason):
        self.cur["skipped"][str(seq)] = reason

    def add_source(self, event_id, source):
        for e in self.ev["events"]:
            if e["id"] == event_id and source not in e["sources"]:
                e["sources"].append(source)

    def next_update_id(self, base):
        n = 1
        while f"{base}-u{n}" in self.ids:
            n += 1
        return f"{base}-u{n}"

    # ── the card ─────────────────────────────────────────────
    def card(self, seq, *, line, title, imp, body, expl, sig_label, sig_symbol, sig_dir, sig_reason,
             conf, deadline, asset, sources, reply_to=None, seqs=None):
        """sources: list of (name, url). sig_dir: up|down|neutral|none. conf: کم|متوسط|زیاد."""
        assert sig_dir in DIRS and conf in ("کم", "متوسط", "زیاد")
        code = self.cur["next_code"]
        self.cur["next_code"] = code + 1
        lbl = next(l for top, l in LABELS if imp <= top)
        p = self.pub(seq)
        jd = subprocess.check_output(["python3", str(ROOT / "scripts" / "jdate.py"), p], text=True).strip()
        if sig_dir == "none":
            sig_line = f"⚠️ سیگنال : ندارد ( {sig_reason} )"
        else:
            sig_line = f"⚠️ سیگنال : {sig_label} = {DIRS[sig_dir]} ( {sig_reason} ) ( اطمینان: {conf} )"
        srcs = " | ".join(f"[[{n}|{u}]]" for n, u in sources)
        text = f"""⚠️ کد خبر : {str(code).translate(FA)}
⚠️ تاریخ خبر: {jd} ({{{{age:{p}}}}})

⚠️ لاین خبری {line}

⚠️ تیتر خبر : {title}
⚠️ درجه اهمیت : {str(imp).translate(FA)} از ۱۰۰ ( {lbl} )

⚠️ متن خبر : {body}

⚠️ توضیح ساده بات : {expl}

{sig_line}
⚠️ مهلت ورود : {deadline}

⚠️ قیمت هنگام خبر : {asset} {{{{price:{sig_symbol}@{p}}}}}


منابع : {srcs}"""
        if reply_to:
            text = "🔄 به‌روزرسانی :\n" + text
            eid = self.next_update_id(reply_to)
        else:
            eid = f"e-{seq}"
        self.ids.add(eid)
        self.out["messages"].append({
            "id": f"m-{seq}", "code": code, "event_id": eid, "reply_to_event": reply_to,
            "created": self.now, "seqs": seqs or [seq], "importance": imp,
            "signal": {"label": sig_label, "symbol": sig_symbol, "dir": sig_dir, "conf": conf, "at": p},
            "text": text})
        self.ev["events"].append({"id": eid, "key": self.inbox[seq]["title"][:100], "ts": self.now,
                                  "seq": seq, "code": code,
                                  "sources": [self.inbox[s]["source"] for s in (seqs or [seq])]})
        return code

    def save(self, last_seq):
        self.cur["last_seq"] = last_seq
        self.cur["last_round_at"] = self.now
        self.cur["updated"] = self.now
        for name, obj in (("outbox.json", self.out), ("events.json", self.ev), ("cursor.json", self.cur)):
            (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        return self.prev_round
