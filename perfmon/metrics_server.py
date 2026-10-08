"""A minimal stdlib-only HTTP server exposing the latest sample as
Prometheus text format on GET /metrics, so a Prometheus server can scrape
perfmon and Grafana can graph the numbers over time. No new dependencies:
uses only http.server/threading from the standard library.

Runs in a background thread; the main sampling loop (whichever UI mode is
active) calls MetricsServer.update(sample) after every sample() so /metrics
always reflects the most recent reading without re-sampling on scrape.
"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import prometheus_export


class _Handler(BaseHTTPRequestHandler):
    server_version = "perfmon-metrics/1.0"

    def log_message(self, fmt, *args):
        pass  # quiet -- don't spam stdout/stderr with per-scrape access logs

    def do_GET(self):
        if self.path.rstrip("/") not in ("", "/metrics"):
            self.send_response(404)
            self.end_headers()
            return
        body = self.server.metrics_server.render().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MetricsServer:
    """Holds the latest sample and serves it as Prometheus text on
    http://0.0.0.0:<port>/metrics until stop() is called."""

    def __init__(self, port, is_multi=False):
        self._lock = threading.Lock()
        self._latest = None
        self._is_multi = is_multi
        self._httpd = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
        self._httpd.metrics_server = self
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self):
        self._thread.start()

    def update(self, sample):
        with self._lock:
            self._latest = sample

    def render(self):
        with self._lock:
            sample = self._latest
        if sample is None:
            return "# perfmon: no sample collected yet\n"
        if self._is_multi:
            return prometheus_export.render_multi(sample)
        return prometheus_export.render(sample)

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()
