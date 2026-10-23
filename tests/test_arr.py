from checks import arr


def quality(id, name, allowed=True):
    return {"quality": {"id": id, "name": name}, "allowed": allowed, "items": []}


def profile(id, name, cutoff=7, upgrade=True, scores=(100, -10000), minimum=0, cutoff_score=10000):
    return {"id": id, "name": name, "upgradeAllowed": upgrade, "cutoff": cutoff, "minFormatScore": minimum,
            "cutoffFormatScore": cutoff_score,
            "items": [quality(1, "SDTV", False), quality(3, "WEBDL-1080p"),
                      {"id": 1001, "name": "WEB 1080p", "allowed": True, "items": [quality(7, "Bluray-1080p")]}],
            "formatItems": [{"format": i, "name": f"cf{i}", "score": s} for i, s in enumerate(scores)]}


def sonarr(**over):
    data = {
        "items": [{"title": "The Wire", "qualityProfileId": 1}, {"title": "Bluey", "qualityProfileId": 1}],
        "profiles": [profile(1, "WEB-1080p")],
        "formats": [{"id": 0, "name": "cf0"}, {"id": 1, "name": "cf1"}],
        "health": [{"source": "UpdateCheck", "type": "ok", "message": ""}],
        "host": {"authenticationMethod": "forms", "authenticationRequired": "enabled", "backupFolder": "Backups",
                 "backupInterval": 7, "backupRetention": 28, "apiKey": "secret", "password": "hunter2"},
        "media": {"setPermissionsLinux": False},
    }
    data.update(over)
    return data


def titles(findings):
    return sorted(f["title"] for f in findings)


def test_a_tidy_sonarr_has_nothing_to_report():
    assert arr.check("sonarr", "Sonarr", sonarr()) == []
    # Nothing was read (an old Sonarr, or the call failed): nothing guessed
    assert arr.check("sonarr", "Sonarr", sonarr(health=None, host=None, media=None, formats=None)) == []


def test_health_messages():
    health = [{"source": "IndexerStatusCheck", "type": "warning", "message": "Indexers unavailable due to failures: Nyaa",
               "wikiUrl": "https://wiki.servarr.com/sonarr/system#indexers-are-unavailable-due-to-failures"},
              {"source": "RootFolderCheck", "type": "error", "message": "Missing root folder: /tv"},
              {"source": "DownloadClientCheck", "type": "error", "message": "Unable to communicate with qBittorrent.",
               "wikiUrl": "javascript:alert(1)"}]
    findings = arr.check("sonarr", "Sonarr", sonarr(health=health))
    assert titles(findings) == ["Sonarr: Indexers unavailable due to failures: Nyaa",
                                "Sonarr: Unable to communicate with qBittorrent"]
    by = {f["title"]: f for f in findings}
    assert by["Sonarr: Unable to communicate with qBittorrent"]["severity"] == "problem"
    assert by["Sonarr: Unable to communicate with qBittorrent"]["link"] is None


def test_security_and_permissions():
    findings = arr.check("radarr", "Radarr", sonarr(host={"authenticationMethod": "none"}, media={"setPermissionsLinux": True}))
    assert titles(findings) == ["Anyone who can reach Radarr can change it", "Radarr changes file permissions itself"]
    assert "hunter2" not in str(findings)
    host = {**sonarr()["host"], "authenticationRequired": "disabledForLocalAddresses"}
    assert titles(arr.check("radarr", "Radarr", sonarr(host=host))) == ["Radarr doesn't ask for a password on your network"]


def test_profiles():
    profiles = [profile(1, "WEB-1080p", minimum=500), profile(2, "HD", scores=(0, 0)),
                profile(3, "Any", cutoff=3, cutoff_score=0), profile(4, "No upgrades", upgrade=False)]
    items = [{"title": t, "qualityProfileId": i} for i, t in enumerate(["A", "B", "C", "D"], 1)]
    findings = arr.check("radarr", "Radarr", sonarr(profiles=profiles, items=items))
    assert titles(findings) == ["Nothing can ever be grabbed for the WEB-1080p profile", "The Any profile never upgrades",
                                "The HD profile ignores your custom formats",
                                "The No upgrades profile never upgrades to better releases"]
    assert next(f for f in findings if "grabbed" in f["title"])["detail"].endswith("1 film on it never download.")
    assert titles(arr.check("radarr", "Radarr", sonarr(formats=[]))) == ["Radarr has no custom formats"]


def test_backups():
    host = {**sonarr()["host"], "backupInterval": 0, "backupFolder": "/backups"}
    snapshot = {"containers": [{"name": "sonarr", "image": "lscr.io/linuxserver/sonarr", "source": "template",
                                "mounts": [{"host": "/mnt/user/appdata/sonarr", "container": "/config"}]}]}
    findings = arr.check("sonarr", "Sonarr", sonarr(host=host), snapshot)
    assert titles(findings) == ["Sonarr doesn't keep backups", "Sonarr's backups are lost when its container is updated"]
    host["backupFolder"] = "/config/Backups"
    assert titles(arr.check("sonarr", "Sonarr", sonarr(host=host), snapshot)) == ["Sonarr doesn't keep backups"]


def test_unused_profiles_recycle_bin_and_availability():
    # A profile nothing uses is reported once, and its own settings aren't checked
    findings = arr.check("sonarr", "Sonarr", sonarr(profiles=[profile(1, "WEB-1080p"), profile(2, "Unused", minimum=99999)]))
    assert titles(findings) == ["1 quality profile in Sonarr isn't used"]
    assert titles(arr.check("sonarr", "Sonarr", sonarr(media={"recycleBin": ""}))) == ["Sonarr deletes files for good"]
    assert arr.check("sonarr", "Sonarr", sonarr(media={"recycleBin": "/data/.recycle"})) == []
    films = [{"title": "Dune", "qualityProfileId": 1, "minimumAvailability": "announced", "monitored": True, "hasFile": False},
             {"title": "Heat", "qualityProfileId": 1, "minimumAvailability": "announced", "monitored": True, "hasFile": True},
             {"title": "Up", "qualityProfileId": 1, "minimumAvailability": "released", "monitored": True, "hasFile": False}]
    findings = arr.check("radarr", "Radarr", sonarr(items=films))
    assert titles(findings) == ["1 film can be grabbed as soon as it is announced"] and findings[0]["items"] == ["Dune"]
