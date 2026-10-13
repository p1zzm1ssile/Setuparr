"""Talking to Sonarr, Radarr, Prowlarr, Seerr, Jellyfin and Plex."""

import requests

import app_info


class ServiceError(Exception):
    pass


# service: (path that answers with the version, header the key goes in)
STATUS = {
    "sonarr": ("/api/v3/system/status", "X-Api-Key"),
    "radarr": ("/api/v3/system/status", "X-Api-Key"),
    "prowlarr": ("/api/v1/system/status", "X-Api-Key"),
    "seerr": ("/api/v1/status", "X-Api-Key"),
    "bazarr": ("/api/system/status", "X-API-KEY"),
    "jellyfin": ("/System/Info", "X-Emby-Token"),
    "plex": ("/identity", "X-Plex-Token"),
}

# Where each app keeps its API
API = {"sonarr": "/api/v3", "radarr": "/api/v3", "prowlarr": "/api/v1", "seerr": "/api/v1"}


class Service:
    def __init__(self, service, label, cfg):
        self.service = service
        self.label = label
        self.url = getattr(cfg, f"{service}_url")
        self.key = getattr(cfg, f"{service}_api_key")
        self.timeout = cfg.request_timeout
        self.api = API.get(service, "")

    def request(self, method, path, body=None, **params):
        headers = {STATUS[self.service][1]: self.key, "Accept": "application/json"}
        if self.service == "jellyfin":
            # Jellyfin 10.11 turns the old X-Emby-Token header off by default and wants this one
            headers["Authorization"] = f'MediaBrowser Token="{self.key}"'
        try:
            response = requests.request(method, self.url + path, params=params or None, json=body, timeout=self.timeout,
                                        headers=headers)
        except requests.RequestException as e:
            raise ServiceError(f"Can't reach {self.label} at {self.url} ({e.__class__.__name__})")
        if response.status_code in (401, 403):
            raise ServiceError(f"{self.label} turned down the {'token' if self.service == 'plex' else 'API key'}")
        if not response.ok:
            raise ServiceError(f"{self.label} answered {response.status_code} for {method} {path}")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise ServiceError(f"{self.label} didn't answer with JSON. Is the address right?")

    def get(self, path, **params):
        return self.request("GET", path, **params)

    def call(self, method, path, body=None):
        """The app's own API, e.g. sonarr.call("GET", "/tag/detail")."""
        return self.request(method, self.api + path, body)

    def version(self):
        data = self.get(STATUS[self.service][0])
        if self.service == "bazarr":
            return (data.get("data") or {}).get("bazarr_version", "")
        if self.service == "jellyfin":
            return data.get("Version", "")
        if self.service == "plex":
            return (data.get("MediaContainer") or {}).get("version", "")
        return data.get("version", "")


def connected(cfg):
    """{service: Service} for every app that's switched on and has an address and key."""
    services = {}
    for key, label, *_ in app_info.SERVICES:
        if getattr(cfg, f"{key}_enabled", False) and getattr(cfg, f"{key}_url", "") and getattr(cfg, f"{key}_api_key", ""):
            services[key] = Service(key, label, cfg)
    return services
