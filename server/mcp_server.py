#!/usr/bin/env python3
"""Radarban MCP server — runs on the VPS, lets Claude (via a claude.ai custom connector) do analyst rounds.

Only a few narrow tools are exposed (no shell, no file access):
  get_rules          – the analyst rules (ANALYST.md) + card field spec
  get_new_items      – inbox items not yet processed (junk pages pre-filtered)
  find_events        – recent published events (for duplicate / update detection)
  submit_round       – write the round's cards / skips through scripts/card.py, advance the cursor
  get_status         – health: collector runs, sources with errors, panel delivery backlog
  get_review         – the «برایند ۶ ساعته» text (dry run, nothing is sent)

Security: the MCP endpoint lives on a secret path  https://<host>/<RADAR_MCP_SECRET>/mcp
(claude.ai custom connectors cannot send custom auth headers, so the secret is in the URL; keep it private).
Run behind Caddy (HTTPS). Listens on 127.0.0.1 only.

Env: RADAR_MCP_SECRET (required, ≥ 32 chars), RADAR_MCP_HOSTS (comma list, e.g. mcp.radarban.com),
     RADAR_DATA (data dir), RADAR_MCP_PORT (default 8787)
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("RADAR_DATA") or ROOT / "data")
os.environ["RADAR_DATA"] = str(DATA)
sys.path.insert(0, str(ROOT / "scripts"))

from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

import card as C  # noqa: E402

SECRET = os.environ.get("RADAR_MCP_SECRET", "")
HOSTS = [h.strip() for h in os.environ.get("RADAR_MCP_HOSTS", "").split(",") if h.strip()]
PORT = int(os.environ.get("RADAR_MCP_PORT", "8787"))
LOCK = DATA / ".round.lock"

# Pages that are never news (quote / option / forum pages from aggregator feeds) — skipped without AI.
JUNK_RE = re.compile(
    r"(Stock Price, News, Quote|stock price, news, quote|latest stock news and headlines|"
    r"Latest Stock News & Headlines|Stock Forum & Discussion|Stock Major Holders|Historical Prices & Data|"
    r"Interactive Stock Chart|Charts, Data & News|SEC Filings|Insider Transactions|Stock Forecast|"
    r"Earnings Calendar|Earnings Call Transcripts|Option Chain|Options Chain|Fund Performance|"
    r"\b(?:call|put) \([A-Z0-9]{10,}\)|\| Indices - Bloomberg)",
)
JUNK_SOURCES = {"yahoo", "bloomberg_gn", "reuters_gn"}


def _load(name, default):
    try:
        return json.loads((DATA / name).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _inbox():
    p = DATA / "inbox.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _is_junk(it) -> bool:
    return it.get("source") in JUNK_SOURCES and bool(JUNK_RE.search(it.get("title", "")))


@contextmanager
def _locked():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


security = TransportSecuritySettings(
    enable_dns_rebinding_protection=bool(HOSTS),
    allowed_hosts=HOSTS + [f"{h}:443" for h in HOSTS] + ["127.0.0.1:%d" % PORT, "localhost:%d" % PORT],
    allowed_origins=["https://claude.ai", "https://claude.com"] + [f"https://{h}" for h in HOSTS],
)

mcp = FastMCP(
    "radarban",
    instructions=(
        "Radarban news analyst tools. A round = get_rules (once per session) → get_new_items → "
        "find_events for anything that might be a duplicate/update → submit_round with a decision for "
        "EVERY returned item (card or skip) → report get_status/submit_round stats to the user in Persian."
    ),
    host="127.0.0.1",
    port=PORT,
    streamable_http_path=f"/{SECRET}/mcp",
    stateless_http=True,
    json_response=True,
    transport_security=security,
)


# ── tools ────────────────────────────────────────────────────────────────
@mcp.tool()
def get_rules() -> str:
    """Analyst rules (format, trading-safety rules, classification) and the card fields for submit_round.
    Read once at the start of every session before writing cards."""
    rules = (ROOT / "ANALYST.md").read_text(encoding="utf-8")
    spec = """
## submit_round card fields (all text in Persian)
seq (inbox seq of the main item), seqs (optional: all merged seqs), line (e.g. کریپتو / فارکس / سهام / طلا / نفت / فدرال رزرو),
title, imp (0–100), body, expl, sig_label (e.g. BTC, USD/JPY), sig_symbol (Yahoo symbol: BTC-USD, JPY=X, ^GSPC, GC=F, CL=F, NVDA…),
sig_dir up|down|neutral|none, sig_reason, conf کم|متوسط|زیاد, deadline, asset, sources [[name,url],…] (url without tracking params),
reply_to (event id like e-910 for an update, else null).
Every item returned by get_new_items must appear in a card (seq/seqs) or in skips {seq: reason}.
Duplicates: skip + add_sources [{event_id, source}].
"""
    return rules + spec


@mcp.tool()
def get_new_items(limit: int = 80) -> dict:
    """Inbox items with seq > cursor.last_seq, oldest first. Quote/option/forum pages are pre-filtered
    (listed only as counts in auto_junk; submit_round skips them automatically)."""
    cur = _load("cursor.json", {})
    last = int(cur.get("last_seq", 0))
    new = [it for it in _inbox() if it["seq"] > last]
    limit = max(1, min(limit, 200))
    real = [it for it in new if not _is_junk(it)]
    items = real[:limit]
    if len(real) > limit:                      # more waiting: stop right after the last returned item
        upto = items[-1]["seq"]
    else:
        upto = max([it["seq"] for it in new] + [last])
    junk = [it["seq"] for it in new if _is_junk(it)]
    return {
        "last_seq_processed": last,
        "process_up_to_seq": upto,
        "previous_round_at": cur.get("last_round_at"),
        "auto_junk_count": len([s for s in junk if s <= upto]),
        "items": [{k: it.get(k) for k in ("seq", "source", "source_name", "tier", "category", "title",
                                         "summary", "url", "published", "headline_only")}
                  for it in items if it["seq"] <= upto],
    }


@mcp.tool()
def find_events(query: str = "", hours: float = 72, limit: int = 40) -> list[dict]:
    """Recent published events (newest first) for duplicate/update checks. query = space-separated
    keywords (any match, case-insensitive), e.g. "bitcoin etf outflow". Empty query = latest events."""
    ev = _load("events.json", {"events": []})["events"]
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    words = [w.lower() for w in query.split() if len(w) > 1]
    out = []
    for e in reversed(ev):
        if e.get("ts", "") < since:
            continue
        key = e.get("key", "")
        if words and not any(w in key.lower() for w in words):
            continue
        out.append({"id": e["id"], "code": e.get("code"), "ts": e.get("ts"), "key": key,
                    "sources": e.get("sources", [])})
        if len(out) >= limit:
            break
    return out


class CardIn(BaseModel):
    seq: int
    seqs: list[int] | None = None
    line: str
    title: str
    imp: int = Field(ge=0, le=100)
    body: str
    expl: str
    sig_label: str
    sig_symbol: str
    sig_dir: Literal["up", "down", "neutral", "none"]
    sig_reason: str
    conf: Literal["کم", "متوسط", "زیاد"]
    deadline: str
    asset: str
    sources: list[list[str]]
    reply_to: str | None = None


class SourceAdd(BaseModel):
    event_id: str
    source: str


def _strip_tracking(url: str) -> str:
    return url.split("?")[0] if "news.google.com" in url or "utm_" in url else url


@mcp.tool()
def submit_round(process_up_to_seq: int, cards: list[CardIn] = [], skips: dict[str, str] = {},  # noqa: B006
                 add_sources: list[SourceAdd] = []) -> dict:  # noqa: B006
    """Save a round: cards are rendered by card.py (news code, explicit signal line, price/age tokens),
    skips get a reason, auto-junk pages are skipped automatically, cursor advances to process_up_to_seq.
    Fails (nothing saved) if any item up to process_up_to_seq has no decision."""
    with _locked():
        R = C.Round()
        last = int(R.cur.get("last_seq", 0))
        pending = {s: it for s, it in R.inbox.items() if last < s <= process_up_to_seq}
        covered = {int(k) for k in skips}
        for c in cards:
            covered |= set(c.seqs or [c.seq])
        junk = {s for s, it in pending.items() if _is_junk(it)}
        missing = sorted(set(pending) - covered - junk)
        if missing:
            return {"ok": False, "error": "no decision for seqs", "missing": missing}
        unknown = sorted(s for s in covered if s not in R.inbox)
        if unknown:
            return {"ok": False, "error": "unknown seqs", "unknown": unknown}
        for c in cards:
            if c.reply_to and c.reply_to not in R.ids:
                return {"ok": False, "error": f"reply_to {c.reply_to} is not a known event id"}
        codes = []
        for c in cards:
            codes.append(R.card(
                c.seq, line=c.line, title=c.title, imp=c.imp, body=c.body, expl=c.expl,
                sig_label=c.sig_label, sig_symbol=c.sig_symbol, sig_dir=c.sig_dir, sig_reason=c.sig_reason,
                conf=c.conf, deadline=c.deadline, asset=c.asset,
                sources=[(s[0], _strip_tracking(s[1])) for s in c.sources if len(s) == 2],
                reply_to=c.reply_to, seqs=c.seqs))
        for k, v in skips.items():
            R.skip(int(k), v)
        for s in junk - covered:
            R.skip(s, "auto: quote/option/forum page (prefilter)")
        for a in add_sources:
            R.add_source(a.event_id, a.source)
        prev = R.save(max(process_up_to_seq, last))
    stats = _round_stats(prev, len(pending), len(cards))
    return {"ok": True, "codes": codes, "stats": stats, "next_code": R.cur["next_code"]}


def _round_stats(since, analysed, sent) -> str:
    try:
        return subprocess.check_output(
            [sys.executable, str(ROOT / "scripts" / "round_stats.py"), since or "1970-01-01T00:00:00Z",
             str(analysed), str(sent)], text=True, env={**os.environ, "RADAR_DATA": str(DATA)}).strip()
    except Exception as ex:  # noqa: BLE001
        return f"(stats unavailable: {ex})"


@mcp.tool()
def get_status() -> dict:
    """Health: last round, waiting items, last collector run, sources with ≥3 consecutive errors,
    cards not yet delivered to the panel."""
    cur = _load("cursor.json", {})
    seen = _load("seen.json", {})
    status = _load("sources_status.json", {})
    runs_p = DATA / "runs.jsonl"
    last_run = json.loads(runs_p.read_text().splitlines()[-1]) if runs_p.exists() and runs_p.read_text().strip() else None
    out = _load("outbox.json", {"messages": []})["messages"]
    delivered = _load("panel_sent.json", {"sent": {}})["sent"]
    return {
        "last_round_at": cur.get("last_round_at"),
        "last_seq": cur.get("last_seq"),
        "collector_seq": seen.get("seq"),
        "waiting_items": max(0, int(seen.get("seq", 0)) - int(cur.get("last_seq", 0))),
        "last_collector_run": last_run,
        "sources_with_errors": [k for k, v in status.items() if isinstance(v, dict) and v.get("consecutive_errors", 0) >= 3],
        "panel_undelivered": [m["id"] for m in out if m["id"] not in delivered][:20],
        "next_code": cur.get("next_code"),
    }


@mcp.tool()
def get_review(hours: float = 6) -> str:
    """The «برایند ۶ ساعته» text for the last N hours (dry run — nothing is sent)."""
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "review.py"), "--hours", str(hours), "--dry-run"],
                          capture_output=True, text=True, timeout=120,
                          env={**os.environ, "RADAR_DATA": str(DATA)}).stdout.strip()


if __name__ == "__main__":
    if len(SECRET) < 32 or not re.fullmatch(r"[A-Za-z0-9_\-]+", SECRET):
        sys.exit("RADAR_MCP_SECRET must be ≥ 32 chars of [A-Za-z0-9_-]")
    mcp.run(transport="streamable-http")
