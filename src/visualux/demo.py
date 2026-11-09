"""Serve only bundled public demo assets on the local loopback interface."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
import sysconfig
from urllib.parse import unquote, urlsplit


class DemoHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        path = unquote(urlsplit(self.path).path)
        allowed = {
            "/", "/index.html", "/guide.html", "/style.css", "/app.js",
            "/lab.html", "/lab-devices.html", "/lab-device.html",
            "/reports.html", "/report-detail.html", "/report-export.html",
            "/device.html", "/device-new.html", "/device-detail.html",
        }
        if path not in allowed:
            self.send_error(404)
            return
        return super().send_head()

    def log_message(self, fmt, *args):
        pass


def server(port: int = 8765) -> ThreadingHTTPServer:
    root = str(files("visualux").joinpath("demo_apps"))
    return ThreadingHTTPServer(("127.0.0.1", port), partial(DemoHandler, directory=root))


def serve(port: int) -> None:
    with server(port) as httpd:
        print(f"VisualUX demo apps: http://127.0.0.1:{httpd.server_port}", flush=True)
        print(f"Setup and walkthroughs: http://127.0.0.1:{httpd.server_port}/guide.html", flush=True)
        print("Choose an app/variant, then evaluate its URL with visualux run. No inference runs on startup.", flush=True)
        examples = Path(sysconfig.get_path("data")) / "share/visualux/examples"
        if examples.is_dir():
            print(f"Copyable model/task configurations: {examples}", flush=True)
        print("Press Ctrl+C to stop.", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
