from checks import downloads, library


def entry(id, name, host="192.168.1.10", port=8080, category="tv-sonarr", key="tvCategory", implementation="QBittorrent",
          enable=True, protocol="torrent"):
    return {"id": id, "name": name, "enable": enable, "protocol": protocol, "implementation": implementation,
            "fields": [{"name": "host", "value": host}, {"name": "port", "value": port}, {"name": key, "value": category}]}


def arr(clients, **over):
    return {"clients": clients, "handling": {"enableCompletedDownloadHandling": True}, **over}


def qbit(**over):
    return {"save_path": "/data/torrents", "incomplete_path": None, "categories": {"radarr": "/data/torrents/movies"},
            "automatic": True, "labels": None, "preallocate": False, "no_login_networks": [], **over}


def container(name, image, *mounts, shares=()):
    return {"name": name, "source": "template", "image": image, "variables": {},
            "mounts": [{"host": h, "container": c, "mode": "rw"} for h, c in mounts]}


def snapshot(*containers, shares=()):
    return {"containers": list(containers), "shares": list(shares), "ignored": []}


def titles(findings):
    return {f["title"]: f for f in findings}


def test_a_tidy_setup_has_nothing_to_report():
    sonarr = arr([entry(1, "qBittorrent")])
    radarr = arr([entry(1, "qBittorrent", category="radarr", key="movieCategory")])
    assert downloads.check_arr("sonarr", "Sonarr", sonarr) == []
    assert downloads.check_shared_categories({"sonarr": ("Sonarr", sonarr), "radarr": ("Radarr", radarr)}) == []
    snap = snapshot(container("qbittorrent", "lscr.io/linuxserver/qbittorrent", ("/mnt/user/data", "/data")),
                    shares=[{"name": "data", "shareUseCache": "yes"}])
    assert downloads.check_client("qbittorrent", "qBittorrent", qbit(), snap) == []


def test_completed_download_handling_off():
    f = titles(downloads.check_arr("radarr", "Radarr", arr([], handling={"enableCompletedDownloadHandling": False})))
    assert f["Completed Download Handling is off in Radarr"]["severity"] == "problem"


def test_a_client_at_localhost():
    f = titles(downloads.check_arr("sonarr", "Sonarr", arr([entry(1, "qBit", host="localhost"),
                                                             entry(2, "Off", host="127.0.0.1", enable=False)])))
    assert f["Sonarr reaches 1 download client at localhost"]["items"] == ["qBit: localhost"]


def test_a_client_with_no_category():
    f = titles(downloads.check_arr("radarr", "Radarr", arr([entry(1, "qBit", key="movieCategory", category="")])))
    assert f["1 download client in Radarr has no category"]["items"] == ["qBit"]


def test_sonarr_and_radarr_sharing_a_category():
    sonarr = arr([entry(1, "qBittorrent", category="media")])
    radarr = arr([entry(1, "qBit", host="192.168.1.10", category="media", key="movieCategory")])
    f = downloads.check_shared_categories({"sonarr": ("Sonarr", sonarr), "radarr": ("Radarr", radarr)})
    assert [x["title"] for x in f] == ["Sonarr and Radarr use the same category, media, in QBittorrent"]
    assert f[0]["items"] == ["Radarr: qBit", "Sonarr: qBittorrent"]


def test_the_same_category_on_two_clients_is_fine():
    sonarr = arr([entry(1, "qBittorrent", category="media")])
    radarr = arr([entry(1, "Other qBit", port=8081, category="media", key="movieCategory")])
    assert downloads.check_shared_categories({"sonarr": ("Sonarr", sonarr), "radarr": ("Radarr", radarr)}) == []


def test_deluge_without_its_label_plugin():
    sonarr = arr([entry(1, "Deluge", implementation="Deluge", port=8112)])
    clients = {"deluge": {"label": "Deluge", "settings": qbit(labels=False)}}
    f = titles(downloads.check_arr("sonarr", "Sonarr", sonarr, clients))
    assert "Sonarr uses a category in Deluge, but Deluge's Label plugin is off" in f
    clients["deluge"]["settings"]["labels"] = True
    assert downloads.check_arr("sonarr", "Sonarr", sonarr, clients) == []


def test_downloads_saved_inside_the_container():
    snap = snapshot(container("qbittorrent", "lscr.io/linuxserver/qbittorrent", ("/mnt/user/appdata/qbittorrent", "/config"),
                              ("/mnt/user/data", "/data")))
    s = qbit(save_path="/downloads", categories={"radarr": "/data/torrents/movies", "tv": "/tv"})
    f = titles(downloads.check_client("qbittorrent", "qBittorrent", s, snap))
    assert f["qBittorrent saves downloads to a folder that isn't mapped to your shares"]["items"] == [
        "Category tv: /tv", "Default save folder: /downloads"]


def test_folders_are_only_checked_when_the_container_is_known():
    s = qbit(save_path="/downloads")
    assert downloads.check_client("qbittorrent", "qBittorrent", s, None) == []
    two = snapshot(container("qb1", "linuxserver/qbittorrent"), container("qb2", "linuxserver/qbittorrent"))
    assert downloads.check_client("qbittorrent", "qBittorrent", s, two) == []


def test_unfinished_downloads_on_another_mapping():
    snap = snapshot(container("transmission", "linuxserver/transmission", ("/mnt/user/data", "/data"),
                              ("/mnt/cache/incomplete", "/incomplete")))
    s = qbit(save_path="/data/torrents", incomplete_path="/incomplete", categories={}, automatic=None)
    assert "Transmission copies every finished download to another folder mapping" in titles(
        downloads.check_client("transmission", "Transmission", s, snap))
    s["incomplete_path"] = "/data/torrents/incomplete"
    assert downloads.check_client("transmission", "Transmission", s, snap) == []


def test_preallocation_on_a_cached_share():
    snap = snapshot(container("qbittorrent", "linuxserver/qbittorrent", ("/mnt/user/data", "/data")),
                    shares=[{"name": "data", "shareUseCache": "yes"}])
    f = titles(downloads.check_client("qbittorrent", "qBittorrent", qbit(preallocate=True), snap))
    assert "qBittorrent reserves disk space on your cache for every download" in f
    snap["shares"][0]["shareUseCache"] = "no"
    assert downloads.check_client("qbittorrent", "qBittorrent", qbit(preallocate=True), snap) == []


def test_manual_torrent_management_and_no_login_networks():
    f = titles(downloads.check_client("qbittorrent", "qBittorrent",
                                      qbit(automatic=False, no_login_networks=["0.0.0.0/0"]), None))
    assert f["qBittorrent's categories don't choose where downloads go"]["items"] == ["radarr"]
    assert f["qBittorrent lets some networks in without a password"]["items"] == ["0.0.0.0/0"]


def test_a_tidy_library_has_nothing_to_report():
    d = {"roots": [{"path": "/data/media/movies", "accessible": True}], "naming": {"renameMovies": True},
         "media": {"copyUsingHardlinks": True, "downloadPropersAndRepacks": "doNotPrefer"},
         "formats": [{"name": "Repack/Proper"}]}
    assert library.check("radarr", "Radarr", d) == []


def test_library_settings_against_the_trash_guides():
    d = {"roots": [{"path": "/movies", "accessible": False}], "naming": {"renameMovies": False},
         "media": {"copyUsingHardlinks": False, "downloadPropersAndRepacks": "preferAndUpgrade"},
         "formats": [{"name": "Repack/Proper"}, {"name": "x265 (HD)"}]}
    f = titles(library.check("radarr", "Radarr", d))
    assert f["Radarr can't reach 1 root folder"]["items"] == ["/movies"]
    assert "Radarr copies downloads instead of hardlinking them" in f
    assert f["Radarr's Propers and Repacks setting works against your Repack/Proper custom format"]["items"] == ["Repack/Proper"]
    assert "Radarr keeps the downloaded file names" in f


def test_propers_and_repacks_only_matter_with_the_custom_format():
    d = {"media": {"downloadPropersAndRepacks": "preferAndUpgrade"}, "formats": [{"name": "x265 (HD)"}]}
    assert library.check("sonarr", "Sonarr", d) == []
