"""The site on http://127.0.0.1:8790 for checking by eye: python3 site/serve.py. Files go out with no-store, so the browser
never shows yesterday's version."""
import functools
import http.server
from pathlib import Path

PORT = 8790


class Server(http.server.ThreadingHTTPServer):
    request_queue_size = 128   # the default 5 reset connections when the browser asks for all the scripts at once
    daemon_threads = True


class Fresh(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    print(f"http://127.0.0.1:{PORT}/")
    Server(("127.0.0.1", PORT), functools.partial(Fresh, directory=str(here))).serve_forever()
