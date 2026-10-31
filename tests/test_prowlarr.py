from datetime import datetime, timedelta, timezone

from checks import prowlarr


def app(id, kind, level="fullSync", prowlarr_url="http://192.168.1.10:9696", base="http://192.168.1.10:8989"):
    return {"id": id, "name": kind, "implementation": kind, "syncLevel": level,
            "fields": [{"name": "prowlarrUrl", "value": prowlarr_url}, {"name": "baseUrl", "value": base},
                       {"name": "apiKey", "value": "secret"}]}


def data(**extra):
    return {"tags": [], "indexers": [{"id": 1, "name": "Good", "enable": True}, {"id": 2, "name": "Flaky", "enable": True}],
            "proxies": [], "apps": [app(1, "Sonarr"), app(2, "Radarr")], "status": [], "health": [], "host": None, **extra}


def titles(findings):
    return sorted(f["title"] for f in findings)


CONNECTED = {"sonarr": "Sonarr", "radarr": "Radarr"}


def test_a_tidy_prowlarr_has_nothing_to_report():
    assert prowlarr.check(data(), None, CONNECTED) == []


def test_missing_apps_sync_levels_and_localhost():
    findings = prowlarr.check(data(apps=[app(1, "Sonarr", "addOnly", prowlarr_url="http://localhost:9696"),
                                         app(3, "Lidarr", "disabled")]),
                              None, {**CONNECTED, "lidarr": "Lidarr"})
    assert titles(findings) == ["Prowlarr doesn't send indexers to Radarr", "Prowlarr only adds indexers to Sonarr",
                                "Prowlarr's Prowlarr Server for Sonarr uses localhost",
                                "Prowlarr's sync to Lidarr is turned off"]
    assert {f["area"] for f in findings} == {"prowlarr"}
    assert "secret" not in str(findings)


def test_localhost_is_fine_on_the_host_network():
    snap = {"containers": [{"name": "prowlarr", "image": "lscr.io/linuxserver/prowlarr", "network": "host"}]}
    assert prowlarr.check(data(apps=[app(1, "Sonarr", base="http://127.0.0.1:8989"), app(2, "Radarr")]),
                          snap, CONNECTED) == []


def test_paused_and_missing_indexers():
    later = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    earlier = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    findings = prowlarr.check(data(status=[{"indexerId": 2, "disabledTill": later}, {"indexerId": 1, "disabledTill": earlier}]),
                              None, CONNECTED)
    assert titles(findings) == ["Prowlarr has paused 1 indexer after errors"]
    assert findings[0]["items"] == ["Flaky"]
    assert titles(prowlarr.check(data(indexers=[]), None, CONNECTED)) == ["Prowlarr has no indexers turned on"]


def test_health_security_and_backups():
    findings = prowlarr.check(data(health=[{"source": "IndexerStatusCheck", "type": "warning", "message": "Indexers down"},
                                           {"source": "ProxyCheck", "type": "error", "message": "Proxy failed."}],
                                   host={"authenticationMethod": "none", "backupInterval": 0}), None, CONNECTED)
    assert titles(findings) == ["Anyone who can reach Prowlarr can change it", "Prowlarr doesn't keep backups",
                                "Prowlarr: Proxy failed"]
