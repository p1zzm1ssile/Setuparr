import io
import json
import tarfile
import zipfile

import pytest

from checks import unraid


def template(name, image, paths=(), variables=()):
    configs = "".join(
        f'<Config Name="{t}" Target="{t}" Default="" Mode="rw" Type="Path" Display="always">{h}</Config>' for h, t in paths)
    configs += "".join(
        f'<Config Name="{k}" Target="{k}" Default="" Mode="" Type="Variable" Display="always" Mask="false">{v}</Config>'
        for k, v in variables)
    xml = (f'<?xml version="1.0"?><Container version="2"><Name>{name}</Name><Repository>{image}</Repository>'
           f'<Network>bridge</Network>{configs}</Container>')
    return (f"plugins/dockerMan/templates-user/my-{name}.xml", xml.encode())


def share(name, use_cache):
    return (f"shares/{name}.cfg", f'shareComment=""\nshareUseCache="{use_cache}"\nshareCachePool="cache"\n'.encode())


def run(files, ignored=()):
    snap = unraid.build_snapshot(unraid.read_upload(files))
    snap["ignored"] = list(ignored)
    return snap, unraid.check(json.loads(json.dumps(snap)))


def titles(findings):
    return [f["title"] for f in findings]


GOOD = [
    template("qbittorrent", "lscr.io/linuxserver/qbittorrent:latest", [("/mnt/user/data/torrents", "/data/torrents")],
             [("PUID", "99"), ("PGID", "100"), ("UMASK", "002")]),
    template("sonarr", "lscr.io/linuxserver/sonarr", [("/mnt/user/appdata/sonarr", "/config"), ("/mnt/user/data", "/data")],
             [("PUID", "99"), ("PGID", "100"), ("UMASK", "002")]),
    template("radarr", "ghcr.io/hotio/radarr:release", [("/mnt/user/appdata/radarr", "/config"), ("/mnt/user/data/", "/data")],
             [("PUID", "99"), ("PGID", "100"), ("UMASK", "002")]),
    share("appdata", "only"), share("data", "yes"),
]


def test_the_trash_layout_has_nothing_to_report():
    _, findings = run(GOOD)
    assert findings == []


def test_passwords_and_keys_are_never_kept():
    files = [template("gluetun", "qmcgaw/gluetun", [("/mnt/user/appdata/gluetun", "/gluetun")],
                      [("OPENVPN_PASSWORD", "hunter2"), ("WIREGUARD_PRIVATE_KEY", "abc123secret"), ("PUID", "99")])]
    snap, _ = run(files)
    text = json.dumps(snap)
    assert "hunter2" not in text and "abc123secret" not in text and "OPENVPN_PASSWORD" not in text
    assert snap["containers"][0]["variables"] == {"PUID": "99"}


def test_separate_download_and_library_mappings_break_hardlinks():
    files = [
        template("qbittorrent", "lscr.io/linuxserver/qbittorrent", [("/mnt/user/downloads", "/downloads")]),
        template("sonarr", "lscr.io/linuxserver/sonarr", [("/mnt/user/downloads", "/downloads"), ("/mnt/user/media/tv", "/tv")]),
    ]
    _, findings = run(files)
    hardlinks = [f for f in findings if "hardlinking" in f["title"]]
    assert len(hardlinks) == 1 and hardlinks[0]["app"] == "Sonarr" and hardlinks[0]["severity"] == "problem"
    # Same container path in both, so no Remote Path Mapping is needed
    assert not [f for f in findings if "different paths" in f["title"]]


def test_different_container_paths_need_a_remote_path_mapping():
    files = [
        template("qbittorrent", "binhex/arch-qbittorrentvpn", [("/mnt/user/data/torrents", "/downloads")]),
        template("radarr", "lscr.io/linuxserver/radarr", [("/mnt/user/data", "/data")]),
    ]
    _, findings = run(files)
    mismatch = [f for f in findings if "different paths" in f["title"]]
    assert len(mismatch) == 1
    assert "/downloads" in mismatch[0]["detail"] and "/data/torrents" in mismatch[0]["detail"]


def test_an_app_that_cant_see_any_downloads():
    files = [
        template("sabnzbd", "lscr.io/linuxserver/sabnzbd", [("/mnt/user/usenet", "/downloads")]),
        template("radarr", "lscr.io/linuxserver/radarr", [("/mnt/user/media", "/media")]),
    ]
    _, findings = run(files)
    assert any("can't see any download client" in t for t in titles(findings))


def test_user_share_and_disk_paths_mixed():
    files = [
        template("qbittorrent", "lscr.io/linuxserver/qbittorrent", [("/mnt/cache/data/torrents", "/data/torrents")]),
        template("sonarr", "lscr.io/linuxserver/sonarr", [("/mnt/user/data", "/data")]),
    ]
    _, findings = run(files)
    assert any(f["title"].startswith("The data share is mapped both") for f in findings)


def test_appdata_on_a_disk_path_is_not_counted_as_mixing():
    files = GOOD + [template("plex", "plexinc/pms-docker", [("/mnt/cache/appdata/plex", "/config"), ("/mnt/user/data/media", "/media")])]
    _, findings = run(files)
    assert findings == []


def test_different_puids_and_umasks():
    files = [
        template("sonarr", "lscr.io/linuxserver/sonarr", [("/mnt/user/data", "/data")], [("PUID", "99"), ("UMASK", "002")]),
        template("radarr", "lscr.io/linuxserver/radarr", [("/mnt/user/data", "/data")], [("PUID", "1000"), ("UMASK", "022")]),
    ]
    _, findings = run(files)
    assert any("PUID" in t for t in titles(findings))
    assert any("UMASK" in t for t in titles(findings))


def test_appdata_on_the_array():
    _, findings = run(GOOD[:3] + [share("appdata", "yes")])
    assert titles(findings) == ["The appdata share can end up on the array"]


def test_ignored_containers_are_not_checked():
    files = [
        template("qbittorrent", "lscr.io/linuxserver/qbittorrent", [("/mnt/user/downloads", "/downloads")]),
        template("sonarr-old", "lscr.io/linuxserver/sonarr", [("/mnt/user/downloads", "/downloads"), ("/mnt/user/tv", "/tv")]),
    ]
    _, findings = run(files, ignored=["sonarr-old"])
    assert not [f for f in findings if f["severity"] == "problem"]


def test_archives_and_compose_files_are_read():
    compose = b"""
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr
    environment:
      - PUID=99
      - API_KEY=dont-keep-me
    volumes:
      - /mnt/user/data:/data
"""
    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w:gz") as t:
        for name, data in [GOOD[0], share("appdata", "only"), ("plugins/compose.manager/projects/arr/docker-compose.yml", compose)]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    zip_bytes = io.BytesIO()
    with zipfile.ZipFile(zip_bytes, "w") as z:
        z.writestr("templates-user/my-radarr.xml", GOOD[2][1])

    snap, findings = run([("unraid-config.tar.gz", tar_bytes.getvalue()), ("more.zip", zip_bytes.getvalue())])
    assert [c["name"] for c in snap["containers"]] == ["qbittorrent", "radarr", "sonarr"]
    assert [s["name"] for s in snap["shares"]] == ["appdata"]
    sonarr = next(c for c in snap["containers"] if c["name"] == "sonarr")
    assert sonarr["source"] == "compose" and sonarr["variables"] == {"PUID": "99"}
    assert "dont-keep-me" not in json.dumps(snap)


def test_nothing_useful_uploaded():
    with pytest.raises(unraid.UploadError):
        unraid.build_snapshot(unraid.read_upload([("notes.txt", b"hello")]))
