"""The Setuparr web UI and its JSON API."""

import base64
import glob
import io
import json
import os
import re
import time
import zipfile
from collections import deque
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import app_info
import utils.auth as auth
import utils.config as config
import utils.status as status
from checks.unraid import UploadError
from core import runner
from services.connections import ServiceError, make
from utils.logger import logger

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
STATIC = {
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
    "/static/login.js": ("login.js", "text/javascript; charset=utf-8"),
    "/static/icon.png": ("icon.png", "image/png"),
    "/static/logo.png": ("logo.png", "image/png"),
}
COOKIE = f"{app_info.SLUG}_session"
WRITE_HEADER = f"X-{app_info.NAME}"
LOG_LINE = re.compile(r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(?P<level>[A-Z]+)\] (?P<message>.*)$")
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'; base-uri 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
SERVICE_LABELS = {key: label for key, label, *_ in app_info.SERVICES}


class Handler(BaseHTTPRequestHandler):
    server_version = app_info.NAME
    sys_version = ""

    def log_message(self, format, *args):
        pass

    # Helpers

    @property
    def ip(self):
        return self.client_address[0]

    def token(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return cookie[COOKIE].value if COOKIE in cookie else ""

    def user(self):
        """The signed-in user, "(local network)" with the LAN bypass, or None."""
        user = auth.session_user(self.token())
        if user:
            return user
        if auth.has_account() and auth.lan_bypass(self.ip, self.headers):
            return "(local network)"
        return None

    def send(self, code, body=b"", content_type="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in {**SECURITY_HEADERS, **(headers or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def redirect(self, location):
        self.send(302, b"", "text/plain", {"Location": location})

    def page(self, name):
        with open(os.path.join(WEB_DIR, name), "rb") as f:
            self.send(200, f.read(), "text/html; charset=utf-8")

    def body(self, limit=1_000_000):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError("Request too large")
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            raise ValueError("Invalid JSON")

    def session_cookie(self, token, max_age):
        return f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}"

    # Routing

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        query = {k: v[0] for k, v in parse_qs(url.query).items()}

        try:
            if path in STATIC:
                name, content_type = STATIC[path]
                with open(os.path.join(WEB_DIR, name), "rb") as f:
                    return self.send(200, f.read(), content_type)

            if path == "/health":
                result = status.health()
                code = 200 if result["healthy"] else 503
                # Without a login, only say whether it's healthy
                return self.send(code, result if self.user() else {"healthy": result["healthy"]})

            if path == "/login":
                if not auth.has_account():
                    return self.redirect("/setup")
                if self.user():
                    return self.redirect("/")
                return self.page("login.html")

            if path == "/setup":
                if auth.has_account():
                    return self.redirect("/login")
                return self.page("setup.html")

            if path == "/api/session":
                return self.send(200, {
                    "user": self.user(),
                    "has_account": auth.has_account(),
                    "needs_setup": not auth.has_account(),
                    "password_rules": [{"id": i, "text": t} for i, t, _ in auth.PASSWORD_RULES],
                    "locked_for": auth.locked_for(self.ip),
                })

            # Everything below needs a login
            if not self.user():
                if path.startswith("/api/"):
                    return self.send(401, {"error": "Please log in"})
                return self.redirect("/login" if auth.has_account() else "/setup")

            if path == "/":
                return self.page("app.html")
            if path == "/api/status":
                return self.send(200, {**status.health(), "version": app_info.VERSION})
            if path == "/api/dashboard":
                return self.send(200, dashboard())
            if path == "/api/unraid":
                snap = runner.snapshot()
                return self.send(200, {"summary": unraid_summary(snap), "leftover": runner.leftover_file()})
            if path == "/api/settings":
                return self.send(200, {
                    "tabs": config.TABS,
                    # Without defaults: they come from environment variables and can
                    # hold API keys. Only values, with secrets masked, go to the browser.
                    "fields": [{k: v for k, v in f.items() if k != "default"} for f in config.FIELDS],
                    "values": config.public_values(),
                    "username": auth.username(),
                })
            if path == "/api/logs":
                return self.send(200, read_logs(query.get("filter", "all")))
            if path == "/api/logs/download":
                return self.send(200, zip_logs(), "application/zip", {
                    "Content-Disposition": f'attachment; filename="{app_info.SLUG}-logs-{time.strftime("%Y%m%d-%H%M%S")}.zip"'
                })

            self.send(404, {"error": "Not found"})
        except ValueError as e:
            self.send(400, {"error": str(e)})
        except Exception as e:
            logger.error(f"Web UI error on {path} | {e}")
            self.send(500, {"error": str(e)})

    def do_POST(self):
        self.handle_write()

    def do_PUT(self):
        self.handle_write()

    def handle_write(self):
        url = urlparse(self.path)
        path = url.path

        # Sent straight from Unraid's terminal with curl, which can't log in: a one-time link instead
        if path == "/api/unraid/push":
            query = parse_qs(url.query)
            return self.unraid_push(query.get("token", [""])[0], query.get("key", [""])[0])

        # Only accept writes from our own pages: a form on another site
        # can't set this header, and the session cookie is SameSite=Strict too
        if self.headers.get(WRITE_HEADER) != "1":
            return self.send(403, {"error": "Missing request header"})

        try:
            # Uploaded Unraid files come base64-encoded in JSON, so allow more for them
            data = self.body(80_000_000 if path == "/api/unraid/upload" and self.user() else 1_000_000)

            if path == "/api/setup":
                if auth.has_account():
                    return self.send(403, {"error": "An account already exists"})
                if auth.locked_for(self.ip):
                    return self.send(429, {"error": "Too many attempts. Try again in a few minutes"})
                if not auth.can_create_account(data.get("code")):
                    # Count it like a failed login, so the code can't be guessed
                    auth.login(self.ip, "", "")
                    logger.warning(f"Wrong setup code entered from {self.ip}")
                    return self.send(403, {"error": f"That setup code isn't right. It's in {app_info.NAME}'s log"})
                auth.set_password(data.get("username"), data.get("password"))
                logger.info(f"Created web UI account {auth.username()}")
                token = auth.login(self.ip, data.get("username"), data.get("password"))
                return self.send(200, {"ok": True}, headers={"Set-Cookie": self.session_cookie(token, auth.SESSION_SECONDS)})

            if path == "/api/login":
                if wait := auth.locked_for(self.ip):
                    return self.send(429, {"error": f"Too many attempts. Try again in {wait // 60 + 1} minute(s)"})
                token = auth.login(self.ip, data.get("username"), data.get("password"))
                if not token:
                    logger.warning(f"Failed web UI login from {self.ip}")
                    if wait := auth.locked_for(self.ip):
                        return self.send(429, {"error": f"Too many attempts. Try again in {wait // 60 + 1} minute(s)"})
                    return self.send(401, {"error": "Wrong username or password"})
                return self.send(200, {"ok": True}, headers={"Set-Cookie": self.session_cookie(token, auth.SESSION_SECONDS)})

            if path == "/api/logout":
                auth.logout(self.token())
                return self.send(200, {"ok": True}, headers={"Set-Cookie": self.session_cookie("", 0)})

            if not self.user():
                return self.send(401, {"error": "Please log in"})

            if path == "/api/settings":
                changes = data.get("values") or {}
                errors = config.update(changes)
                if errors:
                    return self.send(400, {"errors": errors})
                logger.info(f"Settings saved ({', '.join(sorted(changes.keys()))})")
                if any(k.split("_")[0] in SERVICE_LABELS for k in changes):
                    runner.run_in_background()
                return self.send(200, {"ok": True, "values": config.public_values()})

            if path == "/api/test":
                return self.send(200, test_connection(data.get("target", ""), data.get("values") or {}))

            if path == "/api/password":
                if self.user() == "(local network)" and not auth.session_user(self.token()):
                    return self.send(403, {"error": "Log in with your password to change it"})
                if not auth.check_password(auth.username(), data.get("current", "")):
                    return self.send(400, {"error": "Current password is wrong"})
                auth.set_password(data.get("username") or auth.username(), data.get("password"))
                logger.info("Web UI password changed")
                token = auth.login(self.ip, data.get("username") or auth.username(), data.get("password"))
                return self.send(200, {"ok": True}, headers={"Set-Cookie": self.session_cookie(token, auth.SESSION_SECONDS)})

            if path == "/api/run":
                if runner.results()["running"]:
                    return self.send(409, {"error": "The checks are already running"})
                runner.run_in_background()
                return self.send(200, {"ok": True, "message": "Checking"})
            if path == "/api/fix":
                return self.send(200, {"ok": True, "message": runner.apply_fix(str(data.get("id", "")))})
            if path == "/api/undo":
                return self.send(200, {"ok": True, "message": runner.undo_fix(str(data.get("id", "")))})
            if path == "/api/dismiss":
                runner.set_dismissed(str(data.get("id", "")), bool(data.get("dismissed")))
                return self.send(200, {"ok": True})

            if path in ("/api/unraid/upload", "/api/unraid/import-folder"):
                if path == "/api/unraid/upload":
                    files = []
                    for f in data.get("files") or []:
                        try:
                            files.append((str(f.get("name", "")), base64.b64decode(f.get("data", ""), validate=True)))
                        except (ValueError, TypeError, AttributeError):
                            raise ValueError(f"{f.get('name', 'A file')} couldn't be read")
                    if not files:
                        raise ValueError("Choose some files first")
                    summary = runner.import_files(files)
                else:
                    summary = runner.import_from_config_folder()
                runner.run_in_background()
                return self.send(200, {"ok": True, "summary": summary})
            if path == "/api/unraid/token":
                return self.send(200, {"token": runner.new_push_token(), "minutes": runner.PUSH_SECONDS // 60})
            if path == "/api/unraid/schedule":
                return self.send(200, {"key": runner.schedule_key(new=bool(data.get("new")))})
            if path == "/api/unraid/delete-leftover":
                runner.delete_leftover()
                return self.send(200, {"ok": True})
            if path == "/api/unraid/ignore":
                runner.set_ignored([str(n) for n in data.get("names") or []])
                runner.run_in_background()
                return self.send(200, {"ok": True})
            if path == "/api/unraid/forget":
                runner.forget_snapshot()
                runner.run_in_background()
                return self.send(200, {"ok": True})

            self.send(404, {"error": "Not found"})
        except (ValueError, UploadError, ServiceError) as e:
            self.send(400, {"error": str(e)})
        except Exception as e:
            logger.error(f"Web UI error on {path} | {e}")
            self.send(500, {"error": str(e)})


    def unraid_push(self, token, key=""):
        """token: a one-time link from the Unraid page. key: the scheduled scan script's key, which also runs the
        checks and answers with three lines for Unraid's notify script (importance, subject, description), or
        "none" when nothing new was found."""
        def text(code, message):
            self.send(code, message + "\n", "text/plain; charset=utf-8")

        scheduled = bool(key) and runner.use_schedule_key(key)
        if not scheduled and not runner.use_push_token(token):
            logger.warning(f"Unraid files sent with a wrong or used link from {self.ip}")
            if key:
                return text(403, "Setuparr: the scheduled scan key isn't right. Copy the script from the Unraid page again.")
            return text(403, "Setuparr: that link has been used or has run out. Get a new one on the Unraid page.")
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= 50_000_000:
            return text(400, "Setuparr: nothing was sent, or it's more than 50 MB.")
        try:
            # Read in memory and never written to disk
            host = {k: self.headers.get(f"X-{k.capitalize()}") for k in ("containers", "appdata", "docker")}
            summary = runner.import_files([("unraid.tar.gz", self.rfile.read(length))],
                                          self.headers.get("X-Hardware"), self.headers.get("X-Unraid-Version"), host)
        except (UploadError, ValueError) as e:
            return text(400, f"Setuparr: {e}")
        if scheduled:
            try:
                report = runner.scan_and_report()
            except ValueError as e:
                return text(503, f"Setuparr: {e}")
            return text(200, "\n".join(report) if report else "none")
        runner.run_in_background()
        return text(200, f"Setuparr: read {len(summary['containers'])} containers and {len(summary['shares'])} shares. "
                         "Nothing was saved on the way. Go back to Setuparr to see the results.")


# API helpers


def unraid_summary(snap):
    from checks.unraid import summary
    return summary(snap) if snap else None


def dashboard():
    return {
        "tagline": app_info.TAGLINE,
        "planned": app_info.PLANNED,
        "results": runner.results(),
        "fixes": runner.fixes()[:20],
        "score": runner.score(runner.results()["findings"]),
        "scores": runner.scores()[-60:],
        "connections": [
            {"key": key, "label": label, "enabled": bool(getattr(config, f"{key}_enabled"))}
            for key, label, *_ in app_info.SERVICES
        ],
    }


def read_logs(kind):
    lines = []
    try:
        with open(config.log_file, errors="replace") as f:
            # Only keep the end of the file, rather than reading all of it into memory
            raw = deque(f, maxlen=max(config.log_lines * 5, 2000))
    except FileNotFoundError:
        raw = []
    for line in raw:
        match = LOG_LINE.match(line.rstrip("\n"))
        entry = match.groupdict() if match else {"time": "", "level": "INFO", "message": line.rstrip("\n")}
        if kind == "issues" and entry["level"] not in ("ERROR", "CRITICAL", "WARNING"):
            continue
        if kind == "actions" and entry["level"] != "ACTION":
            continue
        lines.append(entry)
    return {"lines": lines[-config.log_lines:]}


def zip_logs():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(glob.glob(os.path.join(config.log_dir, "*.log*"))):
            z.write(path, os.path.basename(path))
    return buffer.getvalue()


def test_connection(target, values):
    """Try a connection with the settings on screen, saved or not."""
    if target not in SERVICE_LABELS:
        return {"ok": False, "message": "Unknown service"}
    label = SERVICE_LABELS[target]
    try:
        cfg = config.preview(values)
        if not getattr(cfg, f"{target}_url"):
            return {"ok": False, "message": "Enter the address first, e.g. http://192.168.1.10"}
        if not getattr(cfg, app_info.secret_key(target)) and target not in app_info.LOGINS:
            return {"ok": False, "message": f"Enter the {'token' if target == 'plex' else 'API key'} first"}
        return {"ok": True, "message": f"Connected to {label} {make(target, label, cfg).version()}".rstrip()}
    except (ServiceError, ValueError) as e:
        return {"ok": False, "message": str(e)}


def serve():
    server = ThreadingHTTPServer(("", config.web_port), Handler)
    server.daemon_threads = True
    logger.info(f"Starting web UI at {config.web_host}:{config.web_port}")
    if not auth.has_account():
        logger.log("STARTUP", f"First run: open the web UI and use setup code {auth.SETUP_CODE} to create your login")
    server.serve_forever()
