import io
import json
import os
import tarfile

import pytest

import utils.config as config
from core import runner
import utils.auth as auth
from test_web import HEADER, PASSWORD, request, server  # noqa: F401 (server is a fixture)
from test_web import login_cookie as _login


@pytest.fixture(autouse=True, scope="module")
def forget_account_afterwards():
    had = auth.has_account()
    yield
    if not had and os.path.exists(auth.AUTH_FILE):
        os.remove(auth.AUTH_FILE)
        auth._account, auth._account_loaded = None, False


def login_cookie(port):
    if not auth.has_account():
        auth.set_password("admin", PASSWORD)
    return _login(port)

TEMPLATE = (b'<?xml version="1.0"?><Container version="2"><Name>sonarr</Name><Repository>lscr.io/linuxserver/sonarr'
            b'</Repository><Network>br0</Network><Config Type="Path" Target="/data">/mnt/user/data</Config>'
            b'<Config Type="Variable" Target="PUID">99</Config><Config Type="Variable" Target="TZ">Europe/London</Config>'
            b'<Config Type="Variable" Target="VPN_PASS">hunter2</Config><Config Type="Port" Target="8989">8989</Config>'
            b'</Container>')


def archive():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        info = tarfile.TarInfo("plugins/dockerMan/templates-user/my-sonarr.xml")
        info.size = len(TEMPLATE)
        t.addfile(info, io.BytesIO(TEMPLATE))
    return buf.getvalue()


def push(port, token, body, headers=None):
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", f"/api/unraid/push?token={token}", body=body, headers=headers or {})
    response = conn.getresponse()
    return response.status, response.read().decode()


def test_files_sent_from_the_terminal_are_read_once_and_nothing_else_is_kept(server):
    cookie = login_cookie(server)
    before = set(os.listdir(config.config_dir))
    _, data = request(server, "POST", "/api/unraid/token", {}, {**HEADER, "Cookie": cookie})
    token = json.loads(data)["token"]

    status, text = push(server, token, archive())
    assert status == 200 and "read 1 containers" in text

    saved = open(runner.SNAPSHOT_FILE).read()
    for secret in ("hunter2", "VPN_PASS", "Europe/London", "br0", "8989"):
        assert secret not in saved
    assert json.loads(saved)["containers"][0]["variables"] == {"PUID": "99"}
    # Only the allow-listed summary and the results were written, never the archive
    new = {n for n in set(os.listdir(config.config_dir)) - before if not n.endswith(".tmp")}
    assert new <= {"unraid.json", "results.json"}

    status, text = push(server, token, archive())
    assert status == 403 and "used" in text


def test_the_terminal_command_sends_the_unraid_version_and_pci_ids(server):
    cookie = login_cookie(server)
    _, data = request(server, "POST", "/api/unraid/token", {}, {**HEADER, "Cookie": cookie})
    status, _ = push(server, json.loads(data)["token"], archive(),
                     {"X-Unraid-Version": 'version="7.1.4"', "X-Hardware": "0300:8086:4680 0200:10ec:8125 "})
    assert status == 200
    saved = json.loads(open(runner.SNAPSHOT_FILE).read())
    assert saved["unraid_version"] == "7.1.4"
    assert [d["vendor"] for d in saved["hardware"]] == ["8086", "10ec"]


def test_a_made_up_link_is_refused(server):
    status, _ = push(server, "guess", archive())
    assert status == 403


def test_a_leftover_file_can_be_deleted(server):
    cookie = login_cookie(server)
    path = os.path.join(config.config_dir, "unraid-config.tar.gz")
    with open(path, "wb") as f:
        f.write(archive())
    _, data = request(server, "GET", "/api/unraid", headers={"Cookie": cookie})
    assert json.loads(data)["leftover"] == "unraid-config.tar.gz"
    request(server, "POST", "/api/unraid/delete-leftover", {}, {**HEADER, "Cookie": cookie})
    assert not os.path.exists(path)
