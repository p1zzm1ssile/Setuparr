from checks import anime, tags


def tag(id, label, **links):
    base = {"id": id, "label": label, "delayProfileIds": [], "importListIds": [], "notificationIds": [],
            "restrictionIds": [], "indexerIds": [], "downloadClientIds": [], "autoTagIds": [], "seriesIds": []}
    return {**base, **links}


def indexer(id, name, tags=(), protocol="torrent", anime_categories=(5070,)):
    return {"id": id, "name": name, "enableRss": True, "enableAutomaticSearch": True, "protocol": protocol,
            "tags": list(tags), "fields": [{"name": "animeCategories", "value": list(anime_categories)}]}


def client(id, name, tags=(), protocol="torrent"):
    return {"id": id, "name": name, "enable": True, "protocol": protocol, "tags": list(tags)}


def series(id, title, genres=("Drama",), type="standard", root="/data/media/tv", profile=1, tags=(), language="English"):
    return {"id": id, "title": title, "genres": list(genres), "seriesType": type, "rootFolderPath": root,
            "qualityProfileId": profile, "tags": list(tags), "originalLanguage": {"name": language}}


def sonarr(**over):
    data = {
        "tags": [tag(1, "anime", seriesIds=[3], indexerIds=[2])],
        "indexers": [indexer(1, "Public"), indexer(2, "Nyaa", tags=[1])],
        "clients": [client(1, "qBittorrent")],
        "items": [series(1, "The Wire"), series(2, "Bluey", genres=("Animation", "Children")),
                  series(3, "Frieren", genres=("Anime", "Animation"), type="anime", root="/data/media/anime", profile=2,
                         tags=[1], language="Japanese")],
        "roots": [{"path": "/data/media/tv"}, {"path": "/data/media/anime"}],
        "profiles": [{"id": 1, "name": "WEB-1080p"}, {"id": 2, "name": "Remux-1080p - Anime"}],
        "naming": {"animeEpisodeFormat": "{Series Title} - S{season:00}E{episode:00} - {absolute:000}"},
    }
    data.update(over)
    return data


def found(findings):
    return {f["title"]: f for f in findings}


def test_a_tidy_sonarr_has_nothing_to_report():
    d = sonarr()
    assert tags.check_arr("sonarr", "Sonarr", d) == []
    assert anime.check(d) == []


def test_an_unused_tag_can_be_deleted():
    d = sonarr(tags=sonarr()["tags"] + [tag(7, "old")])
    f = found(tags.check_arr("sonarr", "Sonarr", d))['The tag "old" isn\'t used in Sonarr']
    assert f["fix"]["action"] == "delete_tag" and f["fix"]["params"] == {"service": "sonarr", "tag_id": 7, "label": "old"}


def test_an_indexer_limited_to_a_tag_nothing_has():
    d = sonarr(tags=[tag(1, "anime", indexerIds=[2])], items=[series(1, "The Wire")])
    f = found(tags.check_arr("sonarr", "Sonarr", d))
    assert 'Nothing has the tag "anime", so what\'s limited to it is never used' in f
    assert f['Nothing has the tag "anime", so what\'s limited to it is never used']["items"] == ["indexer: Nyaa"]


def test_every_indexer_tagged_leaves_untagged_series_with_none():
    d = sonarr(indexers=[indexer(2, "Nyaa", tags=[1])])
    titles = found(tags.check_arr("sonarr", "Sonarr", d))
    f = titles["Every torrent indexer in Sonarr has a tag, so untagged series have none"]
    assert f["severity"] == "problem" and f["items"] == ["Bluey", "The Wire"]


def test_similar_tags():
    d = sonarr(tags=sonarr()["tags"] + [tag(4, "4k", seriesIds=[1]), tag(5, "4-k", seriesIds=[2])])
    assert "Sonarr has tags that look like the same thing" in found(tags.check_arr("sonarr", "Sonarr", d))


def test_prowlarr_flaresolverr_and_apps():
    d = {"tags": [tag(1, "flaresolverr", indexerProxyIds=[1]), tag(2, "sonarr-only", applicationIds=[1])],
         "indexers": [{"id": 1, "name": "1337x", "enable": True, "tags": []}],
         "proxies": [{"id": 1, "name": "FlareSolverr", "implementation": "FlareSolverr", "tags": [1]},
                     {"id": 2, "name": "FS2", "implementation": "FlareSolverr", "tags": []}],
         "apps": [{"id": 1, "name": "Sonarr", "syncLevel": "fullSync", "tags": [2]}]}
    titles = found(tags.check_prowlarr(d))
    assert 'No indexer shares a tag with the FlareSolverr proxy "FlareSolverr"' in titles
    assert 'The FlareSolverr proxy "FS2" has no tag, so no indexer uses it' in titles
    assert "Prowlarr sends no indexers to Sonarr" in titles


def test_seerr_tags_that_were_deleted():
    d = {"servers": [{"kind": "sonarr", "settings": {"id": 0, "name": "Sonarr", "tags": [1, 9], "animeTags": [8]},
                      "live_tags": [{"id": 1, "label": "requests"}]}]}
    titles = found(tags.check_seerr(d))
    assert titles['Seerr tags requests for "Sonarr" with tags that no longer exist in Sonarr']["items"] == ["Tag #9"]
    assert 'Seerr tags anime requests for "Sonarr" with tags that no longer exist in Sonarr' in titles


def test_anime_set_to_standard_can_be_fixed():
    d = sonarr(items=sonarr()["items"] + [series(4, "One Piece", genres=("Anime",), root="/data/media/anime", profile=2)])
    f = found(anime.check(d))["1 anime series not set to the Anime series type"]
    assert f["fix"]["params"] == {"series_ids": [4], "type": "anime"}


def test_japanese_animation_is_only_suggested():
    d = sonarr(items=sonarr()["items"] + [series(5, "Pokemon", genres=("Animation",), language="Japanese")])
    f = found(anime.check(d))["1 series may be anime"]
    assert f["severity"] == "tip" and f["fix"] is None


def test_anime_in_the_wrong_folder_and_profile():
    d = sonarr(items=sonarr()["items"] + [series(6, "Naruto", genres=("Anime",), type="anime", root="/data/media/tv", profile=1)])
    titles = found(anime.check(d))
    assert "1 anime series outside your anime folder (/data/media/anime)" in titles
    assert "1 anime series not using an anime quality profile" in titles


def test_anime_naming_and_indexers():
    d = sonarr(naming={"animeEpisodeFormat": "{Series Title} - S{season:00}E{episode:00}"},
               indexers=[indexer(1, "Public", anime_categories=())])
    titles = found(anime.check(d))
    assert "Sonarr's anime episode format has no absolute episode number" in titles
    assert "None of Sonarr's indexers search the anime category" in titles


def test_seerr_without_anime_settings():
    seerr = {"servers": [{"kind": "sonarr", "settings": {"id": 0, "name": "Sonarr"}, "live_tags": []}]}
    assert 'Seerr sends anime requests for "Sonarr" to your normal TV setup' in found(anime.check(sonarr(), seerr))
