"""Talking to qBittorrent, Transmission and Deluge.

Setuparr only reads their settings. Each client's settings() returns the same
shape, so the checks don't need to know which client it is:

    {"save_path": folder new downloads go to,
     "incomplete_path": folder unfinished downloads wait in, or None,
     "categories": {category or label: its own folder, or ""},
     "automatic": whether categories pick the folder (qBittorrent), or None,
     "labels": whether categories can be used at all (Deluge's Label plugin), or None,
     "preallocate": whether disk space is reserved for every download up front (qBittorrent), or None,
     "no_login_networks": networks let in without a password (qBittorrent)}
"""

import itertools

import requests

import app_info

from services.connections import ServiceError


class Client:
    def __init__(self, service, label, cfg):
        self.service = service
        self.label = label
        self.url = getattr(cfg, f"{service}_url")
        self.username = getattr(cfg, f"{service}_username", "")
        self.password = getattr(cfg, app_info.secret_key(service))
        self.timeout = cfg.request_timeout
        self.session = requests.Session()

    def send(self, method, url, **kwargs):
        try:
            return self.session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as e:
            raise ServiceError(f"Can't reach {self.label} at {self.url} ({e.__class__.__name__})")

    def json(self, response, what):
        try:
            return response.json()
        except ValueError:
            raise ServiceError(f"{self.label} didn't answer {what} with JSON. Is the address right?")

    def turned_down(self):
        return ServiceError(f"{self.label} turned down the username or password" if self.username
                            else f"{self.label} turned down the password")


class QBittorrent(Client):
    """qBittorrent's Web API (v2). It checks the Referer, so one is always sent."""

    logged_in = False

    def send(self, method, path, **kwargs):
        response = super().send(method, self.url + path, headers={"Referer": self.url}, **kwargs)
        if response.status_code == 403 and path == "/api/v2/auth/login":
            raise ServiceError(f"{self.label} has blocked Setuparr's address after too many wrong passwords. "
                               "Wait for the ban to end, or restart qBittorrent")
        return response

    def get(self, path):
        if not self.logged_in and self.password:
            # Up to qBittorrent 5.1 a sign-in answers 200 with "Ok." or "Fails.". From 5.2 it answers 204 with no
            # text when signed in, and 401 for a wrong password.
            response = self.send("POST", "/api/v2/auth/login", data={"username": self.username, "password": self.password})
            if not response.ok or response.text.strip() == "Fails.":
                raise self.turned_down()
            self.logged_in = True
        response = self.send("GET", path)
        if response.status_code in (401, 403):
            raise ServiceError(f"{self.label} wants a username and password") if not self.password else self.turned_down()
        if not response.ok:
            raise ServiceError(f"{self.label} answered {response.status_code} for {path}")
        return response

    def version(self):
        return self.get("/api/v2/app/version").text.strip()

    def settings(self):
        prefs = self.json(self.get("/api/v2/app/preferences"), "for its settings")
        categories = self.json(self.get("/api/v2/torrents/categories"), "for its categories") or {}
        whitelist = prefs.get("bypass_auth_subnet_whitelist") or ""
        return {
            "save_path": prefs.get("save_path") or "",
            "incomplete_path": prefs.get("temp_path") if prefs.get("temp_path_enabled") else None,
            "categories": {name: (c or {}).get("savePath") or "" for name, c in categories.items()},
            "automatic": bool(prefs.get("auto_tmm_enabled")),
            "labels": None,
            "preallocate": bool(prefs.get("preallocate_all")),
            # What happens when a torrent reaches its share ratio or seeding time limit: 0 stop, 1 remove,
            # 2 super seeding, 3 remove with its files
            "seeding": {"ratio": prefs.get("max_ratio") if prefs.get("max_ratio_enabled") else None,
                        "minutes": prefs.get("max_seeding_time") if prefs.get("max_seeding_time_enabled") else None,
                        "action": prefs.get("max_ratio_act")},
            "no_login_networks": ([n.strip() for n in whitelist.replace(",", "\n").splitlines() if n.strip()]
                                  if prefs.get("bypass_auth_subnet_whitelist_enabled") else []),
        }


class Transmission(Client):
    """Transmission's RPC. It answers 409 with a session id to send back, as protection against forged requests."""

    session_id = ""

    def rpc(self, method):
        url = self.url if self.url.endswith("/rpc") else self.url + "/transmission/rpc"
        auth = (self.username, self.password) if self.username or self.password else None
        for _ in range(2):
            response = self.send("POST", url, json={"method": method}, auth=auth,
                                 headers={"X-Transmission-Session-Id": self.session_id})
            if response.status_code != 409:
                break
            self.session_id = response.headers.get("X-Transmission-Session-Id", "")
        if response.status_code == 401:
            raise ServiceError(f"{self.label} wants a username and password") if not auth else self.turned_down()
        if response.status_code == 403:
            raise ServiceError(f"{self.label} doesn't let Setuparr's address in. Add it to rpc-whitelist in "
                               "Transmission's settings.json, or turn rpc-whitelist-enabled off")
        if not response.ok:
            raise ServiceError(f"{self.label} answered {response.status_code} for {method}")
        data = self.json(response, f"for {method}")
        if data.get("result") != "success":
            raise ServiceError(f"{self.label} answered {method} with: {data.get('result')}")
        return data.get("arguments") or {}

    def version(self):
        return str(self.rpc("session-get").get("version", "")).split(" ")[0]

    def settings(self):
        s = self.rpc("session-get")
        return {
            "save_path": s.get("download-dir") or "",
            "incomplete_path": s.get("incomplete-dir") if s.get("incomplete-dir-enabled") else None,
            "categories": {},  # Sonarr and Radarr use their category as a folder under the download folder
            "automatic": None,
            "labels": None,
            "preallocate": None,
            "seeding": None,
            "no_login_networks": [],
        }


class Deluge(Client):
    """Deluge's web UI JSON-RPC. The web UI talks to a separate daemon; when it isn't connected to one yet,
    Setuparr connects it to the first daemon it knows, as opening the web UI would."""

    ready = False

    def __init__(self, *args):
        super().__init__(*args)
        self.ids = itertools.count(1)

    def call(self, method, *params):
        response = self.send("POST", self.url.removesuffix("/json") + "/json",
                             json={"method": method, "params": list(params), "id": next(self.ids)})
        if not response.ok:
            raise ServiceError(f"{self.label} answered {response.status_code} for {method}")
        data = self.json(response, f"for {method}")
        if data.get("error"):
            message = (data["error"] or {}).get("message", "")
            if "Not authenticated" in message:
                raise self.turned_down()
            raise ServiceError(f"{self.label} answered {method} with: {message}")
        return data.get("result")

    def connect(self):
        if self.ready:
            return
        if not self.call("auth.login", self.password):
            raise self.turned_down()
        if not self.call("web.connected"):
            hosts = self.call("web.get_hosts") or []
            if not hosts:
                raise ServiceError(f"{self.label}'s web UI isn't connected to a Deluge daemon, and doesn't know any")
            self.call("web.connect", hosts[0][0])
            if not self.call("web.connected"):
                raise ServiceError(f"{self.label}'s web UI couldn't connect to its Deluge daemon")
        self.ready = True

    def version(self):
        self.connect()
        return self.call("daemon.get_version") or ""

    def settings(self):
        self.connect()
        c = self.call("core.get_config_values", ["download_location", "move_completed", "move_completed_path"]) or {}
        labels = "Label" in (self.call("core.get_enabled_plugins") or [])
        categories = {}
        if labels:
            for name in self.call("label.get_labels") or []:
                options = self.call("label.get_options", name) or {}
                categories[name] = options.get("move_completed_path", "") if options.get("apply_move_completed") else ""
        # Deluge downloads to download_location, then moves finished downloads when move_completed is on
        finished = c.get("move_completed_path") if c.get("move_completed") else None
        return {
            "save_path": finished or c.get("download_location") or "",
            "incomplete_path": c.get("download_location") if finished else None,
            "categories": categories,
            "automatic": None,
            "labels": labels,
            "preallocate": None,
            "seeding": None,
            "no_login_networks": [],
        }


CLIENTS = {"qbittorrent": QBittorrent, "transmission": Transmission, "deluge": Deluge}
