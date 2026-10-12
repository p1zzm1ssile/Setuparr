"""Talking to Sonarr, Radarr, Prowlarr, Seerr, Bazarr, Jellyfin and Plex."""

import requests


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


class Service:
    def __init__(self, service, label, cfg):
        self.service = service
        self.label = label
        self.url = getattr(cfg, f"{service}_url")
        self.key = getattr(cfg, f"{service}_api_key")
        self.timeout = cfg.request_timeout

    def get(self, path, **params):
        header = STATUS[self.service][1]
        try:
            response = requests.get(self.url + path, params=params, timeout=self.timeout,
                                    headers={header: self.key, "Accept": "application/json"})
        except requests.RequestException as e:
            raise ServiceError(f"Can't reach {self.label} at {self.url} ({e.__class__.__name__})")
        if response.status_code in (401, 403):
            raise ServiceError(f"{self.label} turned down the {'token' if self.service == 'plex' else 'API key'}")
        if not response.ok:
            raise ServiceError(f"{self.label} answered {response.status_code} for {path}")
        try:
            return response.json()
        except ValueError:
            raise ServiceError(f"{self.label} didn't answer with JSON. Is the address right?")

    def version(self):
        data = self.get(STATUS[self.service][0])
        if self.service == "bazarr":
            return (data.get("data") or {}).get("bazarr_version", "")
        if self.service == "jellyfin":
            return data.get("Version", "")
        if self.service == "plex":
            return (data.get("MediaContainer") or {}).get("version", "")
        return data.get("version", "")
