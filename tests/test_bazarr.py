import json

from checks import bazarr, unraid


def settings(**general):
    return {"general": {"use_sonarr": True, "use_radarr": True, "enabled_providers": ["opensubtitlescom", "podnapisi"],
                        "serie_default_enabled": True, "serie_default_profile": 1, "movie_default_enabled": True,
                        "movie_default_profile": 1, "path_mappings": [], "path_mappings_movie": [], **general},
            "sonarr": {"ip": "192.168.1.10", "apikey": "secret"}, "radarr": {"ip": "radarr", "apikey": "secret"},
            "backup": {"frequency": "Weekly"}}


def bazarr_data(**over):
    data = {"settings": settings(), "health": [],
            "providers": [{"name": "opensubtitlescom", "status": "Good", "retry": "-"}]}
    data.update(over)
    return data


def titles(findings):
    return sorted(f["title"] for f in findings)


def template(name, image, paths):
    configs = "".join(f'<Config Type="Path" Target="{t}">{h}</Config>' for h, t in paths)
    xml = f'<Container version="2"><Name>{name}</Name><Repository>{image}</Repository>{configs}</Container>'
    return (f"my-{name}.xml", xml.encode())


def snapshot(*templates):
    return json.loads(json.dumps(unraid.build_snapshot(unraid.read_upload(list(templates)))))


def test_a_tidy_bazarr_has_nothing_to_report():
    snap = snapshot(template("sonarr", "lscr.io/linuxserver/sonarr", [("/mnt/user/data", "/data")]),
                    template("bazarr", "lscr.io/linuxserver/bazarr", [("/mnt/user/data", "/data")]))
    assert bazarr.check(bazarr_data(), snap, ["sonarr", "radarr"]) == []


def test_health_connections_providers_and_profiles():
    s = settings(use_radarr=False, serie_default_enabled=False, enabled_providers=[])
    s["sonarr"]["ip"] = "localhost"
    s["backup"]["frequency"] = "Manually"
    health = [{"object": "Missing languages profile", "issue": "You must create at least one languages profile."}]
    findings = bazarr.check(bazarr_data(settings=s, health=health), None, ["sonarr", "radarr"])
    assert titles(findings) == ["Bazarr doesn't back itself up", "Bazarr doesn't get subtitles for Radarr's films",
                                "Bazarr has no subtitle providers", "Bazarr looks for Sonarr at localhost",
                                "Bazarr: Missing languages profile", "New series get no subtitles in Bazarr"]
    assert "secret" not in str(findings)


def test_nothing_connected_and_every_provider_throttled():
    providers = [{"name": "a", "status": "TooManyRequests"}, {"name": "b", "status": "ServiceUnavailable"}]
    findings = bazarr.check(bazarr_data(settings=settings(use_sonarr=False, use_radarr=False), providers=providers))
    assert titles(findings) == ["All of Bazarr's subtitle providers are paused", "Bazarr isn't connected to Sonarr or Radarr"]


def test_paths_bazarr_cant_see_or_sees_elsewhere():
    sonarr = template("sonarr", "lscr.io/linuxserver/sonarr", [("/mnt/user/data", "/data")])
    radarr = template("radarr", "lscr.io/linuxserver/radarr", [("/mnt/user/films", "/movies")])
    sub = template("bazarr", "lscr.io/linuxserver/bazarr", [("/mnt/user/data", "/media")])
    findings = bazarr.check(bazarr_data(), snapshot(sonarr, radarr, sub))
    assert titles(findings) == ["Bazarr can't see Radarr's films", "Bazarr sees Sonarr's series at a different path"]
    # A path mapping fixes the difference
    data = bazarr_data(settings=settings(path_mappings=[["/data", "/media"]]))
    assert titles(bazarr.check(data, snapshot(sonarr, sub))) == []
