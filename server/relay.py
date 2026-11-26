#!/usr/bin/env python3
"""Panel → VPS delivery (route 2 of 2: pull). The panel's cron asks for queued items it hasn't got yet.

GET /relay/items?after=<rseq>&limit=<n>
  headers: X-Radar-Ts (unix s), X-Radar-Sig = hex HMAC-SHA256(PANEL_SECRET, "<ts>.<query string>")
  → {"ok": true, "items": [...rendered items with rseq > after...], "last_rseq": N}
`after` doubles as an acknowledgement: everything with rseq ≤ after is marked delivered (via=pull).

Listens on 127.0.0.1 only; Caddy forwards https://<mcp host>/relay/* here.
Env: PANEL_SECRET, RADAR_DATA, RADAR_RELAY_PORT (default 8788)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store  # noqa: E402

SECRET = os.environ.get("PANEL_SECRET", "")
PORT = int(os.environ.get("RADAR_RELAY_PORT", "8788"))


class H(BaseHTTPRequestHandler):
    server_version = "RadarbanRelay/1.0"

    def log_message(self, *a):  # keep journald quiet
        pass

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        u = urlsplit(self.path)
        if u.path.rstrip("/") not in ("/relay/items", "/items"):
            return self._json(404, {"ok": False})
        if len(SECRET) < 32:
            return self._json(500, {"ok": False, "error": "PANEL_SECRET not set"})
        ts = self.headers.get("X-Radar-Ts", "")
        sig = self.headers.get("X-Radar-Sig", "")
        if not ts.isdigit() or abs(time.time() - int(ts)) > 300:
            return self._json(401, {"ok": False, "error": "bad or old timestamp"})
        want = hmac.new(SECRET.encode(), f"{ts}.{u.query}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, sig):
            return self._json(401, {"ok": False, "error": "bad signature"})
        q = parse_qs(u.query)
        try:
            after = max(0, int(q.get("after", ["0"])[0]))
            limit = min(200, max(1, int(q.get("limit", ["100"])[0])))
        except ValueError:
            return self._json(400, {"ok": False, "error": "bad params"})
        with store.locked() as st:
            store.mark_delivered(st, upto_rseq=after, via="pull")
            items = sorted((it for it in st["items"] if it["rseq"] > after), key=lambda x: x["rseq"])[:limit]
            last = st["next_rseq"] - 1
        store.health_update(True, "pull")
        return self._json(200, {"ok": True, "items": [store.payload_item(it) for it in items], "last_rseq": last})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
