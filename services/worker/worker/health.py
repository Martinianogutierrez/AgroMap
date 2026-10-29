from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

log = logging.getLogger(__name__)

_STATE = {"ok": True, "worker_id": "", "jobs_done": 0}


def set_state(**kwargs: object) -> None:
    _STATE.update(kwargs)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path not in ("/healthz", "/health", "/"):
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(
            {
                "status": "ok" if _STATE.get("ok") else "degraded",
                "worker_id": _STATE.get("worker_id"),
                "jobs_done": _STATE.get("jobs_done"),
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_health_server(port: int | None = None) -> threading.Thread:
    port = int(port or os.environ.get("HEALTH_PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), _Handler)

    def run() -> None:
        log.info("health listening on :%s", port)
        server.serve_forever()

    t = threading.Thread(target=run, name="healthz", daemon=True)
    t.start()
    return t
