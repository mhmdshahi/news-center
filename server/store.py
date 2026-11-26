"""Panel delivery queue (data/panel_store.json) shared by the pusher, the relay and review.py.

Every card / review is rendered ONCE (live price & age resolved at that moment) and gets an increasing
`rseq`. The same rendered item is then delivered by whichever route works first:
  * push  – VPS → panel   (server/push_panel.py, every 30 s)
  * pull  – panel → VPS   (panel cron → server/relay.py)
Delivery is idempotent on the panel (upsert by msg_id), so both routes can run at the same time.
Nothing is ever dropped: undelivered items stay queued (in order) until a route succeeds.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("RADAR_DATA") or ROOT / "data")
STORE = DATA / "panel_store.json"
HEALTH = DATA / "panel_health.json"
KEEP_DAYS = 7


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(path: Path, obj) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def locked():
    """Exclusive access to the store for read-modify-write."""
    DATA.mkdir(parents=True, exist_ok=True)
    with open(DATA / ".panel_store.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            st = _read(STORE, {"next_rseq": 1, "items": []})
            yield st
            cutoff = time.time() - KEEP_DAYS * 86400
            st["items"] = [it for it in st["items"]
                           if not it.get("delivered_at") or _ts(it.get("queued_at")) > cutoff]
            _write(STORE, st)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def read() -> dict:
    return _read(STORE, {"next_rseq": 1, "items": []})


def _ts(iso: str | None) -> float:
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def enqueue(st: dict, item: dict) -> bool:
    """Add a rendered item (must have msg_id). Returns False if it is already queued."""
    if any(it["msg_id"] == item["msg_id"] for it in st["items"]):
        return False
    item = dict(item, rseq=st["next_rseq"], queued_at=now_iso(), delivered_at=None, via=None)
    st["next_rseq"] += 1
    st["items"].append(item)
    return True


def mark_delivered(st: dict, upto_rseq: int | None = None, msg_ids: set | None = None, via: str = "") -> int:
    n = 0
    for it in st["items"]:
        if it.get("delivered_at"):
            continue
        if (upto_rseq is not None and it["rseq"] <= upto_rseq) or (msg_ids and it["msg_id"] in msg_ids):
            it["delivered_at"], it["via"] = now_iso(), via
            n += 1
    return n


def health_update(ok: bool, route: str, err: str = "") -> dict:
    h = _read(HEALTH, {})
    if ok:
        h.update({"last_ok": now_iso(), "last_ok_route": route, "fail_since": None, "last_error": None})
    else:
        h.setdefault("fail_since", None)
        if not h["fail_since"]:
            h["fail_since"] = now_iso()
        h["last_error"] = f"{route}: {err}"[:300]
    _write(HEALTH, h)
    return h


def health() -> dict:
    return _read(HEALTH, {})


def payload_item(it: dict) -> dict:
    """What the panel receives (queue bookkeeping fields removed)."""
    return {k: v for k, v in it.items() if k not in ("queued_at", "delivered_at", "via")}
