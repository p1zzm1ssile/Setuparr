from checks import seerr


def server(kind="sonarr", id=0, live=True, **settings):
    base = {"id": id, "name": f"{kind.capitalize()} {id}", "isDefault": True, "is4k": False, "activeProfileId": 4,
            "activeDirectory": "/data/media/tv"}
    found = {"profiles": [{"id": 4, "name": "WEB-1080p"}], "rootFolders": [{"id": 1, "path": "/data/media/tv/"}],
             "tags": []}
    return {"kind": kind, "settings": {**base, **settings}, "live": found if live else None}


def titles(findings):
    return sorted(f["title"] for f in findings)


def test_a_tidy_seerr_has_nothing_to_report():
    assert seerr.check({"servers": [server(), server("radarr", activeDirectory="/data/media/tv",
                                                     minimumAvailability="released")]}) == []


def test_profiles_and_folders_that_are_gone():
    findings = seerr.check({"servers": [server(activeProfileId=9, activeProfileName="Any",
                                               activeDirectory="/tv", activeAnimeProfileId=4)]})
    assert titles(findings) == ['Seerr sends requests to "Sonarr 0" with a quality profile that no longer exists',
                                'Seerr sends requests to "Sonarr 0" with a root folder Sonarr doesn\'t have']
    assert "it was Any" in findings[0]["detail"] or "it was Any" in findings[1]["detail"]


def test_no_default_server_and_unreachable_servers():
    findings = seerr.check({"servers": [server(isDefault=False), server(id=1, is4k=True, isDefault=False, live=False)]})
    assert titles(findings) == ["Seerr can't reach Sonarr \"Sonarr 1\"", "Seerr has no default Sonarr for 4K TV requests",
                                "Seerr has no default Sonarr for TV requests"]


def test_films_requested_before_release():
    findings = seerr.check({"servers": [server("radarr", activeDirectory="/data/media/tv", minimumAvailability="announced")]})
    assert titles(findings) == ["Films requested through Seerr can be grabbed as soon as they're announced"]
