"""Isolated browser fixture: one test backend and preserved/stripped HTTP proxies.

python tests/ui_server.py --data-dir tmp/ui-test --port 10990
Only binds loopback; credentials and generated users are synthetic test data.
"""

import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.main import create_app
from app.backends.mock import MockBackend

PREFIX = "/services/umeko/tts"
TOKEN = "local-browser-test-" + "x" * 32


class BrowserBackend(MockBackend):
    def __init__(self):
        self.progress_gate = threading.Event()

    def synthesize(self, job):
        job.report_progress("loading_model")
        time.sleep(0.05)
        job.report_progress("synthesizing")
        if job.text == "fixture-failure":
            raise RuntimeError(
                'Synthetic model failure <img src=x onerror="window.traceInjected=true">'
            )
        if job.text.startswith("fixture-hold"):
            self.progress_gate.clear()
            if not self.progress_gate.wait(30):
                raise RuntimeError("browser progress fixture was not released")
        return super().synthesize(job)


def proxy_handler(port, strip):
    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def forward(self):
            if not (self.path == PREFIX or self.path.startswith(PREFIX + "/")):
                self.send_error(404)
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            path = self.path[len(PREFIX) :] or "/" if strip else self.path
            headers = {
                k: v
                for k, v in self.headers.items()
                if k.lower() not in ("connection", "transfer-encoding")
            }
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            try:
                connection.request(self.command, path, body=body, headers=headers)
                response = connection.getresponse()
                data = response.read()
                self.send_response(response.status)
                for name, value in response.getheaders():
                    if name.lower() not in (
                        "connection",
                        "transfer-encoding",
                        "content-length",
                    ):
                        self.send_header(name, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)
            finally:
                connection.close()

        do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = forward

    return Proxy


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=10990)
    args = parser.parse_args()
    settings = Settings(
        data_dir=args.data_dir.resolve(),
        backend="mock",
        admin_token=TOKEN,
        base_url=PREFIX,
        workers=4,
    )
    backend = BrowserBackend()
    app = create_app(settings, backend)

    @app.post("/api/test/release-progress")
    async def release_progress():
        backend.progress_gate.set()
        return {"released": True}

    if not app.state.registry.get("Demo_Voice"):
        app.state.registry.create(
            "Demo_Voice", description="Synthetic browser test fixture"
        )
    servers = []
    for offset, strip in [(1, False), (2, True)]:
        server = ThreadingHTTPServer(
            ("127.0.0.1", args.port + offset), proxy_handler(args.port, strip)
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
    import uvicorn

    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        for server in servers:
            server.shutdown()
