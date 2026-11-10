import ipaddress
import json
import os

import pytest

import utils.auth as auth
import utils.config as config

PASSWORD = "Correct-horse-1"


@pytest.fixture
def proxies(monkeypatch):
    monkeypatch.setattr(config, "trusted_proxies", [ipaddress.ip_network("192.168.1.10/32")])


def test_forwarding_headers_are_ignored_unless_the_proxy_is_trusted(monkeypatch):
    monkeypatch.setattr(config, "trusted_proxies", [])
    # Anyone can write this header, so it mustn't let a visitor dodge the lockout
    assert auth.client_ip("203.0.113.5", {"X-Forwarded-For": "1.2.3.4"}) == "203.0.113.5"


def test_behind_a_trusted_proxy_each_visitor_is_counted_on_their_own(proxies):
    assert auth.client_ip("192.168.1.10", {"X-Forwarded-For": "198.51.100.7"}) == "198.51.100.7"
    # The proxy adds the address it saw at the end: an address the visitor made up comes earlier and is skipped
    assert auth.client_ip("192.168.1.10", {"X-Forwarded-For": "1.2.3.4, 198.51.100.7"}) == "198.51.100.7"
    assert auth.client_ip("192.168.1.10", {"X-Real-IP": "198.51.100.8"}) == "198.51.100.8"
    assert auth.client_ip("192.168.1.10", {"X-Forwarded-For": "not an address"}) == "192.168.1.10"
    assert auth.client_ip("192.168.1.10", {}) == "192.168.1.10"


def test_wrong_passwords_through_the_proxy_dont_lock_everyone_out(proxies):
    auth._failures.clear()
    attacker = auth.client_ip("192.168.1.10", {"X-Forwarded-For": "198.51.100.66"})
    for _ in range(auth.MAX_FAILURES):
        auth.login(attacker, "admin", "wrong")
    assert auth.locked_for(attacker)
    assert not auth.locked_for(auth.client_ip("192.168.1.10", {"X-Forwarded-For": "198.51.100.7"}))
    auth._failures.clear()


@pytest.fixture
def account():
    had = os.path.exists(auth.AUTH_FILE)
    saved = open(auth.AUTH_FILE).read() if had else None
    auth.set_password("admin", PASSWORD)
    yield
    if had:
        with open(auth.AUTH_FILE, "w") as f:
            f.write(saved)
    elif os.path.exists(auth.AUTH_FILE):
        os.remove(auth.AUTH_FILE)
    auth._account, auth._account_loaded = None, False
    auth._failures.clear()


def restart():
    """What Setuparr knows after a restart: only what's on disk."""
    auth._sessions = None
    auth._account, auth._account_loaded = None, False


def test_logins_survive_a_restart_and_only_a_hash_is_saved(account):
    token = auth.login("127.0.0.1", "admin", PASSWORD)
    restart()
    assert auth.session_user(token) == "admin"
    saved = open(auth.SESSIONS_FILE).read()
    assert token not in saved
    assert oct(os.stat(auth.SESSIONS_FILE).st_mode & 0o777) == "0o600"

    auth.logout(token)
    restart()
    assert auth.session_user(token) is None


def test_expired_logins_are_not_loaded(account):
    token = auth.login("127.0.0.1", "admin", PASSWORD)
    with open(auth.SESSIONS_FILE) as f:
        saved = json.load(f)
    for entry in saved.values():
        entry[1] = 1
    with open(auth.SESSIONS_FILE, "w") as f:
        json.dump(saved, f)
    restart()
    assert auth.session_user(token) is None


def test_a_password_change_or_reset_signs_everyone_out(account):
    token = auth.login("127.0.0.1", "admin", PASSWORD)
    auth.set_password("admin", "Another-horse-2")
    restart()
    assert auth.session_user(token) is None

    token = auth.login("127.0.0.1", "admin", "Another-horse-2")
    # Deleting auth.json to reset a forgotten password mustn't leave old logins working
    os.remove(auth.AUTH_FILE)
    restart()
    assert auth.session_user(token) is None


def test_a_broken_sessions_file_just_means_logging_in_again(account):
    with open(auth.SESSIONS_FILE, "w") as f:
        f.write("{not json")
    restart()
    assert auth.session_user("anything") is None
    assert auth.login("127.0.0.1", "admin", PASSWORD)
