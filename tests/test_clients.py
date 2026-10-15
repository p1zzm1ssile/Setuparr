"""The download client connections, against small stand-ins that answer the way each client does."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from services.clients import CLIENTS
from services.connections import ServiceError, connected


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def answer(self, status, body, headers=()):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        return self.rfile.read(int(self.headers.get("Content-Length", 0)))


class QBittorrent(Fake):
    def do_POST(self):
        if self.path == "/api/v2/auth/login":
            ok = self.body() == b"username=admin&password=right" and self.headers.get("Referer")
            return self.answer(200, b"Ok." if ok else b"Fails.", [("Set-Cookie", "SID=abc; path=/")] if ok else [])

    def do_GET(self):
        if self.headers.get("Cookie") not in ("SID=abc", "QBT_SID_8080=abc"):
            return self.answer(403, b"Forbidden")
        if self.path == "/api/v2/app/version":
            return self.answer(200, b"v5.0.0")
        if self.path == "/api/v2/app/preferences":
            return self.answer(200, {"save_path": "/data/torrents", "temp_path_enabled": True, "temp_path": "/data/torrents/incomplete",
                                     "auto_tmm_enabled": False, "preallocate_all": True,
                                     "bypass_auth_subnet_whitelist_enabled": True,
                                     "bypass_auth_subnet_whitelist": "192.168.1.0/24\n10.0.0.0/8"})
        if self.path == "/api/v2/torrents/categories":
            return self.answer(200, {"radarr": {"name": "radarr", "savePath": "/data/torrents/movies"},
                                     "tv-sonarr": {"name": "tv-sonarr", "savePath": ""}})


class QBittorrent52(QBittorrent):
    """qBittorrent 5.2 answers a sign-in with 204 and no text, or 401 for a wrong password."""

    def do_POST(self):
        if self.path == "/api/v2/auth/login":
            if self.body() == b"username=admin&password=right" and self.headers.get("Referer"):
                return self.answer(204, b"", [("Set-Cookie", "QBT_SID_8080=abc; HttpOnly; SameSite=Strict; path=/")])
            return self.answer(401, b"Unauthorized")


class Transmission(Fake):
    def do_POST(self):
        request = json.loads(self.body())
        if self.headers.get("Authorization") != "Basic YWRtaW46cmlnaHQ=":  # admin:right
            return self.answer(401, b"Unauthorized")
        if self.headers.get("X-Transmission-Session-Id") != "token":
            return self.answer(409, b"", [("X-Transmission-Session-Id", "token")])
        assert request["method"] == "session-get"
        self.answer(200, {"result": "success", "arguments": {"version": "4.0.5 (a6fe2a64aa)", "download-dir": "/data/torrents",
                                                             "incomplete-dir": "/incomplete", "incomplete-dir-enabled": False}})


class Deluge(Fake):
    connected = False

    def do_POST(self):
        request = json.loads(self.body())
        method, params = request["method"], request["params"]
        logged_in = self.headers.get("Cookie") == "_session_id=xyz"
        if method == "auth.login":
            ok = params == ["right"]
            return self.answer(200, {"result": ok, "error": None, "id": request["id"]},
                               [("Set-Cookie", "_session_id=xyz; Path=/json")] if ok else [])
        if not logged_in:
            return self.answer(200, {"result": None, "error": {"message": "Not authenticated", "code": 1}, "id": request["id"]})
        results = {
            "web.connected": Deluge.connected,
            "web.get_hosts": [["host1", "127.0.0.1", 58846, "localclient"]],
            "daemon.get_version": "2.1.1",
            "core.get_config_values": {"download_location": "/data/torrents/incomplete", "move_completed": True,
                                       "move_completed_path": "/data/torrents/complete"},
            "core.get_enabled_plugins": ["Label"],
            "label.get_labels": ["radarr", "tv-sonarr"],
        }
        if method == "web.connect":
            Deluge.connected = params == ["host1"]
            result = []
        elif method == "label.get_options":
            result = ({"apply_move_completed": True, "move_completed_path": "/data/torrents/movies"} if params == ["radarr"]
                      else {"apply_move_completed": False, "move_completed_path": ""})
        else:
            result = results[method]
        self.answer(200, {"result": result, "error": None, "id": request["id"]})


@pytest.fixture(scope="module")
def servers():
    started = {}
    for key, handler in (("qbittorrent", QBittorrent), ("qbittorrent52", QBittorrent52), ("transmission", Transmission),
                         ("deluge", Deluge)):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started[key] = (server, f"http://127.0.0.1:{server.server_address[1]}")
    yield {key: url for key, (_, url) in started.items()}
    for server, _ in started.values():
        server.shutdown()


def client(key, url, password="right", username="admin"):
    cfg = SimpleNamespace(**{f"{key}_url": url, f"{key}_username": username, f"{key}_password": password,
                             "request_timeout": 5})
    return CLIENTS[key](key, key.capitalize(), cfg)


def test_qbittorrent(servers):
    c = client("qbittorrent", servers["qbittorrent"])
    assert c.version() == "v5.0.0"
    assert c.settings() == {"save_path": "/data/torrents", "incomplete_path": "/data/torrents/incomplete",
                            "categories": {"radarr": "/data/torrents/movies", "tv-sonarr": ""}, "automatic": False,
                            "labels": None, "preallocate": True, "no_login_networks": ["192.168.1.0/24", "10.0.0.0/8"]}


def test_qbittorrent_5_2_signs_in(servers):
    assert client("qbittorrent", servers["qbittorrent52"]).version() == "v5.0.0"
    with pytest.raises(ServiceError, match="turned down the username or password"):
        client("qbittorrent", servers["qbittorrent52"], password="wrong").version()


def test_transmission_sends_back_its_session_id(servers):
    c = client("transmission", servers["transmission"])
    assert c.version() == "4.0.5"
    assert c.settings()["incomplete_path"] is None


def test_deluge_connects_its_web_ui_to_the_daemon(servers):
    c = client("deluge", servers["deluge"], username="")
    assert c.version() == "2.1.1"
    assert Deluge.connected
    s = c.settings()
    assert s["save_path"] == "/data/torrents/complete" and s["incomplete_path"] == "/data/torrents/incomplete"
    assert s["categories"] == {"radarr": "/data/torrents/movies", "tv-sonarr": ""} and s["labels"] is True


@pytest.mark.parametrize("key, message", [("qbittorrent", "turned down the username or password"),
                                          ("transmission", "turned down the username or password"),
                                          ("deluge", "turned down the password")])
def test_a_wrong_password(servers, key, message):
    with pytest.raises(ServiceError, match=message):
        client(key, servers[key], password="wrong", username="" if key == "deluge" else "admin").version()


def test_a_client_without_a_password_is_still_connected():
    cfg = SimpleNamespace(qbittorrent_enabled=True, qbittorrent_url="http://192.168.1.10:8080", qbittorrent_password="",
                          qbittorrent_username="", request_timeout=5)
    assert list(connected(cfg)) == ["qbittorrent"]


def test_qbittorrent_without_a_password_says_it_needs_one(servers):
    with pytest.raises(ServiceError, match="wants a username and password"):
        client("qbittorrent", servers["qbittorrent"], password="").version()
