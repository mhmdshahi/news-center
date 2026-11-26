#!/usr/bin/env python3
"""News Center collector — layer 1 & 2 (code, no AI).

Reads every RSS source in config/sources.yaml in parallel, and appends only
genuinely new, relevant items to data/inbox.jsonl for the analyst.

Rules (see config/rules.yaml):
  * cold start      – the first time a source is read, everything already in it is
                      marked "seen" and nothing is forwarded (no flood of old news)
  * freshness       – items published more than max_age_minutes ago are dropped;
                      broken/future timestamps fall back to first-seen time
  * URL dedup       – canonicalised URL (no utm/amp/m./www/trailing slash) + GUID
  * title dedup     – exact normalised-title fingerprint, then fuzzy similarity;
                      titles that differ only in their numbers are kept (new figure)
  * junk filter     – blacklist regexes and sponsored/press-release feed tags
  * relevance       – keyword / ticker / watchlist filters for general sources
Every dropped item is logged with its reason in data/dropped.jsonl.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests
import yaml
from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("RADAR_DATA") or ROOT / "data")
CONFIG = ROOT / "config"

SEEN_FILE = DATA / "seen.json"
STATUS_FILE = DATA / "sources_status.json"
INBOX_FILE = DATA / "inbox.jsonl"
DROPPED_FILE = DATA / "dropped.jsonl"

UA = os.environ.get(
    "COLLECTOR_USER_AGENT",
    "Mozilla/5.0 (compatible; NewsCenterBot/0.1; +https://github.com/mhmdshahi/news-center)",
)
SEC_UA = os.environ.get("SEC_USER_AGENT", "NewsCenter research bot admin@example.com")
TIMEOUT = 20

TRACKING_PARAMS = re.compile(
    r"^(utm_.*|fbclid|gclid|mc_cid|mc_eid|ref|ref_src|src|source|cmp|cmpid|guccounter|"
    r"guce_referrer.*|feed|feedname|rss|tpcc|taid|yptr|ncid|soc_src|soc_trk|mod|xid|"
    r"sref|smid|partner|cid|campaign|__twitter_impression)$",
    re.I,
)
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


# ── helpers ──────────────────────────────────────────────────────────────
def now_ts() -> float:
    return time.time()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def atomic_write(path: Path, text: str) -> None:
    """Write via temp file + rename so readers (MCP server, pusher) never see a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_json(path: Path, obj) -> None:
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def sha(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def canonical_url(url: str) -> str:
    if not url:
        return ""
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = p.netloc.lower()
    for pre in ("www.", "m.", "amp.", "mobile."):
        if host.startswith(pre):
            host = host[len(pre):]
    path = re.sub(r"/(amp|amp\.html)/?$", "", p.path)
    path = re.sub(r"\.amp(\.html)?$", "", path).rstrip("/") or "/"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=False)
                             if not TRACKING_PARAMS.match(k)))
    return urlunsplit(("https", host, path, query, ""))


def normalise_title(title: str, source_name: str = "") -> str:
    t = title or ""
    # strip trailing " - Reuters", " | Bloomberg", "— CoinDesk"
    t = re.sub(r"\s+[\-\|–—:]\s+[^\-\|–—:]{2,40}$", "", t) if re.search(r"\s[\-\|–—]\s", t) else t
    t = t.lower()
    t = re.sub(r"^(breaking|update\s*\d*|updated|exclusive|watch|live)\s*[:\-–—]\s*", "", t)
    t = re.sub(r"[^\w\s$%.,]", " ", t)
    t = re.sub(r"(?<!\d)[.,]|[.,](?!\d)", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def numbers_of(text: str) -> list[str]:
    return sorted({n.replace(",", "") for n in NUM_RE.findall(text or "") if len(n) >= 1})


def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"&nbsp;|&#160;", " ", s)
    s = re.sub(r"&amp;", "&", s)
    s = re.sub(r"&[a-z#0-9]+;", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def entry_time(e) -> float | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        st = e.get(key)
        if st:
            try:
                return float(calendar.timegm(st))
            except (TypeError, ValueError, OverflowError):
                continue
    return None


# ── fetch ────────────────────────────────────────────────────────────────
def fetch(src: dict, status: dict) -> tuple[dict, list, str | None]:
    headers = {"User-Agent": SEC_UA if "sec.gov" in src["url"] else UA,
               "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5"}
    st = status.get(src["id"], {})
    if st.get("etag"):
        headers["If-None-Match"] = st["etag"]
    if st.get("last_modified"):
        headers["If-Modified-Since"] = st["last_modified"]
    r = None
    for attempt in range(3):
        try:
            r = requests.get(src["url"], headers=headers, timeout=TIMEOUT)
        except requests.RequestException as ex:
            if attempt == 2:
                return src, [], f"network: {type(ex).__name__}"
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            time.sleep(4 * (attempt + 1))
            continue
        break
    if r.status_code == 304:
        return src, [], None
    if r.status_code >= 400:
        return src, [], f"http {r.status_code}"
    feed = feedparser.parse(r.content)
    if not feed.entries and feed.bozo:
        return src, [], f"parse: {type(feed.bozo_exception).__name__}"
    st["etag"] = r.headers.get("ETag")
    st["last_modified"] = r.headers.get("Last-Modified")
    status[src["id"]] = st
    return src, feed.entries, None


# ── main ─────────────────────────────────────────────────────────────────
def main() -> int:
    DATA.mkdir(exist_ok=True)
    sources = [s for s in yaml.safe_load((CONFIG / "sources.yaml").read_text())["sources"]
               if s.get("enabled", True)]
    rules = yaml.safe_load((CONFIG / "rules.yaml").read_text())
    fr, sim = rules["freshness"], rules["similarity"]
    blacklist = [re.compile(p, re.I) for p in rules["blacklist"]]
    bad_tags = {t.lower() for t in rules.get("blacklist_tags", [])}
    keywords = [re.compile(p, re.I) for p in rules["keywords"]]
    ticker_re = re.compile(rules["ticker_pattern"])
    watch = [w.upper() for w in rules.get("watchlist", [])]

    t0 = now_ts()
    memory = fr["memory_hours"] * 3600
    seen = load_json(SEEN_FILE, {"urls": {}, "titles": {}, "recent": [], "seq": 0})
    status = load_json(STATUS_FILE, {})

    # prune memory
    seen["urls"] = {k: v for k, v in seen["urls"].items() if t0 - v < memory}
    seen["titles"] = {k: v for k, v in seen["titles"].items() if t0 - v < memory}
    seen["recent"] = [r for r in seen["recent"] if t0 - r["ts"] < memory]

    gn = [s for s in sources if "news.google.com" in s["url"]]
    other = [s for s in sources if s not in gn]

    def fetch_gn_sequential():
        out = []
        for i, s in enumerate(gn):
            if i:
                time.sleep(2.5)
            out.append(fetch(s, status))
        return out

    with ThreadPoolExecutor(max_workers=16) as pool:
        gn_future = pool.submit(fetch_gn_sequential)
        results = list(pool.map(lambda s: fetch(s, status), other)) + gn_future.result()

    new_items, dropped = [], []
    stats = {"sources_total": len(sources), "sources_ok": 0, "sources_failed": 0, "entries": 0, "unseen": 0, "new": 0, "dropped": 0}

    def drop(src, title, url, reason, detail=""):
        dropped.append({"ts": iso(t0), "source": src["id"], "title": (title or "")[:200],
                        "url": url, "reason": reason, "detail": detail})

    for src, entries, err in results:
        st = status.setdefault(src["id"], {})
        st["last_checked"] = iso(t0)
        if err:
            st["last_error"] = err
            st["consecutive_errors"] = st.get("consecutive_errors", 0) + 1
            stats["sources_failed"] += 1
            continue
        st["last_error"] = None
        st["consecutive_errors"] = 0
        st["last_ok"] = iso(t0)
        stats["sources_ok"] += 1
        cold = not st.get("initialized")
        st["entries_last"] = len(entries) if entries else st.get("entries_last", 0)

        for e in entries:
            stats["entries"] += 1
            title = strip_html(e.get("title", ""))
            link = e.get("link") or ""
            guid = e.get("id") or e.get("guid") or link
            curl = canonical_url(link)
            url_keys = {sha("u:" + curl)} if curl else set()
            if guid:
                url_keys.add(sha("g:" + src["id"] + ":" + guid))
            ntitle = normalise_title(title)
            tkey = sha("t:" + ntitle) if ntitle else None

            already = any(k in seen["urls"] for k in url_keys)
            # remember URL/GUID always (keeps re-dated republishes out)
            for k in url_keys:
                seen["urls"].setdefault(k, t0)

            if cold:
                if tkey:
                    seen["titles"].setdefault(tkey, t0)
                    seen["recent"].append({"t": ntitle, "n": numbers_of(ntitle), "ts": t0, "src": src["id"]})
                continue
            if already:
                continue  # silently: seen before (not logged, would be noise)
            stats["unseen"] += 1
            if not title:
                drop(src, title, link, "no_title")
                continue

            # freshness
            pub = entry_time(e)
            ts_basis = "published"
            if pub is None or pub - t0 > fr["future_tolerance_minutes"] * 60:
                pub, ts_basis = t0, "first_seen"
            age_min = (t0 - pub) / 60
            if age_min > fr["max_age_minutes"]:
                drop(src, title, link, "old", f"{age_min:.0f} min")
                continue

            # junk
            tags = {(t.get("term") or "").lower() for t in e.get("tags", []) if isinstance(t, dict)}
            if tags & bad_tags:
                drop(src, title, link, "junk_tag", ",".join(sorted(tags & bad_tags)))
                continue
            hit = next((p.pattern for p in blacklist if p.search(title)), None)
            if hit:
                drop(src, title, link, "junk_title", hit)
                continue

            summary = strip_html(e.get("summary", "") or e.get("description", ""))[:600]
            text = f"{title} {summary}"

            # relevance
            flt = src.get("filter", "all")
            if flt == "keywords" and not any(k.search(text) for k in keywords):
                drop(src, title, link, "irrelevant")
                continue
            if flt == "ticker" and not (ticker_re.search(text) or any(k.search(title) for k in keywords)):
                drop(src, title, link, "irrelevant", "no ticker")
                continue
            if flt == "watchlist" and not any(w in title.upper() for w in watch):
                drop(src, title, link, "not_watchlist")
                continue

            # title dedup: exact then fuzzy (numbers must match to count as duplicate)
            if tkey and tkey in seen["titles"]:
                drop(src, title, link, "dup_title_exact")
                continue
            nums = numbers_of(ntitle)
            dup_of = None
            for r in seen["recent"]:
                if fuzz.token_sort_ratio(ntitle, r["t"]) >= sim["title_threshold"]:
                    if sim.get("numbers_must_match", True) and nums and r["n"] and nums != r["n"]:
                        continue
                    dup_of = r
                    break
            if dup_of:
                drop(src, title, link, "dup_title_similar", f"{dup_of['src']}: {dup_of['t'][:80]}")
                continue

            if tkey:
                seen["titles"][tkey] = t0
            seen["recent"].append({"t": ntitle, "n": nums, "ts": t0, "src": src["id"]})
            seen["seq"] += 1
            new_items.append({
                "seq": seen["seq"],
                "id": sha("u:" + curl) if curl else sha("t:" + ntitle),
                "source": src["id"], "source_name": src["name"], "tier": src["tier"],
                "category": src["category"],
                "title": title, "summary": summary, "url": link,
                "published": iso(pub), "time_basis": ts_basis,
                "first_seen": iso(t0),
            })

        if cold:
            st["initialized"] = True
            st["initialized_at"] = iso(t0)

    # enrich headline-only items with the article's own description (og:description / meta description)
    def enrich(it):
        if len(it["summary"]) >= 80 or "news.google.com" in it["url"]:
            return
        try:
            r = requests.get(it["url"], headers={"User-Agent": UA}, timeout=8)
            if r.status_code != 200:
                return
            html = r.text[:200000]
            m = (re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']{40,})', html, re.I)
                 or re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{40,})', html, re.I))
            if m:
                it["summary"] = strip_html(m.group(1))[:600]
                it["summary_from"] = "article_meta"
        except requests.RequestException:
            pass

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(enrich, new_items[:20]))
    for it in new_items:
        it["headline_only"] = "news.google.com" in it["url"] or len(it["summary"]) < 40 or strip_html(it["summary"]).lower().startswith(it["title"].lower()[:40])

    # write outputs
    new_items.sort(key=lambda x: x["published"])
    for i, it in enumerate(new_items):  # keep seq in chronological order
        it["seq"] = seen["seq"] - len(new_items) + 1 + i
    stats["new"], stats["dropped"] = len(new_items), len(dropped)

    inbox = [json.loads(l) for l in INBOX_FILE.read_text(encoding="utf-8").splitlines() if l.strip()] \
        if INBOX_FILE.exists() else []
    cutoff = iso(t0 - memory)
    inbox = [x for x in inbox if x["first_seen"] >= cutoff] + new_items
    atomic_write(INBOX_FILE, "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in inbox))

    old_drops = [json.loads(l) for l in DROPPED_FILE.read_text(encoding="utf-8").splitlines() if l.strip()] \
        if DROPPED_FILE.exists() else []
    drops_cut = iso(t0 - 24 * 3600)
    old_drops = [x for x in old_drops if x["ts"] >= drops_cut] + dropped
    DROPPED_FILE.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in old_drops), encoding="utf-8")

    seen["recent"] = seen["recent"][-6000:]
    save_json(SEEN_FILE, seen)
    status["_last_run"] = {"at": iso(t0), "duration_s": round(now_ts() - t0, 1), **stats}
    runs_file = DATA / "runs.jsonl"
    runs = [json.loads(l) for l in runs_file.read_text(encoding="utf-8").splitlines() if l.strip()] \
        if runs_file.exists() else []
    runs = [x for x in runs if x["at"] >= iso(t0 - 48 * 3600)]
    runs.append({**status["_last_run"], "seq_last": seen["seq"],
                 "failed": sorted(k for k, v in status.items()
                                  if not k.startswith("_") and v.get("last_checked") == iso(t0) and v.get("last_error"))})
    atomic_write(runs_file, "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in runs))
    save_json(STATUS_FILE, status)

    print(json.dumps(status["_last_run"]))
    failed = {k: v.get("last_error") for k, v in status.items() if not k.startswith("_") and v.get("last_error")}
    if failed:
        print("FAILED SOURCES:", json.dumps(failed, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
