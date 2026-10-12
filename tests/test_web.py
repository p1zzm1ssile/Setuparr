import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

import app_info
import utils.auth as auth
import utils.config as config
import utils.web as web

HEADER = {web.WRITE_HEADER: "1", "Content-Type": "application/json"}
PASSWORD = "Correct-horse-1"


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()


def request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
    response = conn.getresponse()
    data = response.read()
    return response, data


def login_cookie(port):
    response, _ = request(port, "POST", "/api/login", {"username": "admin", "password": PASSWORD}, HEADER)
    assert response.status == 200
    return response.getheader("Set-Cookie").split(";")[0]


def test_first_run_needs_the_setup_code(server):
    response, _ = request(server, "GET", "/")
    assert response.status == 302 and response.getheader("Location") == "/setup"

    response, _ = request(server, "POST", "/api/setup", {"code": "nope", "username": "admin", "password": PASSWORD}, HEADER)
    assert response.status == 403
    # The failed guess counts towards the lockout; clear it for the next steps
    auth._failures.clear()

    response, _ = request(server, "POST", "/api/setup",
                          {"code": auth.SETUP_CODE, "username": "admin", "password": PASSWORD}, HEADER)
    assert response.status == 200
    assert auth.has_account()


def test_api_needs_a_login(server):
    response, _ = request(server, "GET", "/api/settings")
    assert response.status == 401


def test_writes_need_the_app_header(server):
    response, _ = request(server, "POST", "/api/login", {"username": "admin", "password": PASSWORD},
                          {"Content-Type": "application/json"})
    assert response.status == 403


def test_pages_send_security_headers(server):
    response, _ = request(server, "GET", "/login")
    assert response.getheader("X-Frame-Options") == "DENY"
    assert "default-src 'self'" in response.getheader("Content-Security-Policy")


def test_settings_never_send_secrets(server):
    cookie = login_cookie(server)
    service = app_info.SERVICES[0][0]
    response, _ = request(server, "PUT", "/api/settings",
                          {"values": {f"{service}_api_key": "super-secret"}}, {**HEADER, "Cookie": cookie})
    assert response.status == 200
    response, data = request(server, "GET", "/api/settings", headers={"Cookie": cookie})
    assert b"super-secret" not in data
    assert json.loads(data)["values"][f"{service}_api_key"] == {"set": True}


def test_a_saved_key_is_not_sent_to_a_new_address(server):
    cookie = login_cookie(server)
    service = app_info.SERVICES[0][0]
    response, data = request(server, "PUT", "/api/settings",
                             {"values": {f"{service}_url": "evil.example:1234"}}, {**HEADER, "Cookie": cookie})
    assert response.status == 400
    assert f"{service}_api_key" in json.loads(data)["errors"]


def test_connection_test_reports_an_unreachable_app(server):
    cookie = login_cookie(server)
    service = app_info.SERVICES[0][0]
    config.update({"request_timeout": 1})
    response, data = request(server, "POST", "/api/test",
                             {"target": service, "values": {f"{service}_url": "127.0.0.1:1", f"{service}_api_key": "x"}},
                             {**HEADER, "Cookie": cookie})
    result = json.loads(data)
    assert response.status == 200 and result["ok"] is False
    assert "Can't reach" in result["message"]


def test_dashboard_lists_every_connection(server):
    cookie = login_cookie(server)
    response, data = request(server, "GET", "/api/dashboard", headers={"Cookie": cookie})
    keys = [c["key"] for c in json.loads(data)["connections"]]
    assert keys == [s[0] for s in app_info.SERVICES]
