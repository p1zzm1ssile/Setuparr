import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from services.connections import Service, ServiceError


class Jellyfin1011(BaseHTTPRequestHandler):
    """Like Jellyfin 10.11 with legacy authorization off: only the Authorization header counts."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        ok = self.headers.get("Authorization") == 'MediaBrowser Token="right-key"'
        body = json.dumps({"Version": "10.11.0"} if ok else {}).encode()
        self.send_response(200 if ok else 401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def jellyfin():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Jellyfin1011)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def service(url, key):
    return Service("jellyfin", "Jellyfin", SimpleNamespace(jellyfin_url=url, jellyfin_api_key=key, request_timeout=5))


def test_jellyfin_10_11_accepts_the_key(jellyfin):
    assert service(jellyfin, "right-key").version() == "10.11.0"


def test_a_wrong_jellyfin_key_is_reported(jellyfin):
    with pytest.raises(ServiceError, match="turned down"):
        service(jellyfin, "wrong").version()
