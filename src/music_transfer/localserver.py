"""One-shot local HTTP server used for browser-based login flows."""

from __future__ import annotations

import http.server
import threading
import time
import webbrowser
from typing import Callable, Dict, Tuple
from urllib.parse import parse_qs, urlparse

# handler(method, path, query, body) -> (status, content_type, body_bytes, done)
Handler = Callable[[str, str, Dict[str, str], bytes], Tuple[int, str, bytes, bool]]


def run_browser_flow(
    host: str,
    port: int,
    open_url: str,
    handler: Handler,
    timeout: float = 300.0,
) -> None:
    """Serve requests on host:port, open the browser, and wait until handler reports done."""
    finished = threading.Event()

    class _RequestHandler(http.server.BaseHTTPRequestHandler):
        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            status, ctype, payload, done = handler(method, parsed.path, query, body)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            if done:
                finished.set()

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def log_message(self, format: str, *args) -> None:  # silence default logging
            pass

    try:
        server = http.server.ThreadingHTTPServer((host, port), _RequestHandler)
    except OSError as exc:
        raise SystemExit(
            f"Could not start local login server on {host}:{port} ({exc}). "
            "Is another program using that port?"
        )

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        print(f"Opening your browser to log in. If it does not open, visit:\n  {open_url}")
        webbrowser.open(open_url)
        deadline = time.monotonic() + timeout
        while not finished.is_set():
            if time.monotonic() > deadline:
                raise SystemExit("Timed out waiting for the browser login to finish.")
            finished.wait(0.5)
        time.sleep(0.3)  # let the final response flush
    finally:
        server.shutdown()
        server.server_close()


def html_page(title: str, message: str) -> bytes:
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>{t}</title>"
        "<style>body{{font-family:system-ui,sans-serif;max-width:36rem;margin:4rem auto;"
        "padding:0 1rem;line-height:1.5}}</style></head><body><h1>{t}</h1><p>{m}</p>"
        "</body></html>"
    ).format(t=title, m=message).encode("utf-8")


def parse_host_port(url: str) -> Tuple[str, int, str]:
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    return host, port, parsed.path or "/"
