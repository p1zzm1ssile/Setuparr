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


def test_the_mover_filling_the_cache_with_media():
    _, findings = run(GOOD[:4] + [share("data", "prefer")])
    assert titles(findings) == ["The mover fills your cache with the data share"]


def test_findings_say_when_the_unraid_settings_were_sent_and_clear_once_sent_again():
    old_radarr = template("radarr", "ghcr.io/hotio/radarr", [("/mnt/user/data", "/data"), ("/mnt/user/data/films", "/films"),
                                                             ("/mnt/user/data/torrents", "/torrents")])
    snap = unraid.build_snapshot(unraid.read_upload(GOOD[:2] + [old_radarr] + GOOD[3:]))
    snap["imported_at"] = 1760392974
    findings = unraid.check(snap)
    assert "Radarr copies every download instead of hardlinking it" in titles(findings)
    assert all(f["unraid_sent"] == 1760392974 for f in findings)
    _, findings = run(GOOD)
    assert findings == []


# Unraid's own settings, plugins and hardware


def cfg(path, **values):
    return (path, "".join(f'{k}="{v}"\r\n' for k, v in values.items()).encode())


def plugin(file, url):
    xml = (f'<?xml version="1.0" standalone="yes"?>\n<!DOCTYPE PLUGIN [\n<!ENTITY name "x">\n<!ENTITY pluginURL "{url}">\n]>\n'
           f'<PLUGIN name="&name;" author="someone" pluginURL="&pluginURL;"><FILE Run="/bin/bash"><INLINE>'
           f'echo secret-script</INLINE></FILE></PLUGIN>')
    return (f"plugins/{file}", xml.encode())


def gpu_template(name, image, devices=(), extra="", variables=()):
    configs = "".join(f'<Config Type="Device" Target="{d}">{d}</Config>' for d in devices)
    configs += "".join(f'<Config Type="Variable" Target="{k}">{v}</Config>' for k, v in variables)
    xml = (f'<?xml version="1.0"?><Container version="2"><Name>{name}</Name><Repository>{image}</Repository>'
           f'<Network>bridge</Network><ExtraParams>{extra}</ExtraParams>{configs}</Container>')
    return (f"plugins/dockerMan/templates-user/my-{name}.xml", xml.encode())


def run_with(files, hardware=None, version=None, moderation=None):
    snap = unraid.build_snapshot(unraid.read_upload(files), hardware, version)
    return snap, unraid.check(json.loads(json.dumps(snap)), moderation)


def test_settings_files_are_read_and_only_the_checked_settings_kept():
    files = GOOD + [cfg("docker.cfg", DOCKER_ENABLED="yes", DOCKER_IMAGE_FILE="/mnt/user/system/docker/docker.img",
                        DOCKER_NETWORK_TYPE="1", DOCKER_SUBNET_br0="192.168.1.0/24"),
                    cfg("share.cfg", shareUserExclusive="yes", fuse_useino="yes", shareSMBEnabled="yes"),
                    cfg("network.cfg", IPADDR="192.168.1.10", **{"BRIDGING[0]": "yes"})]
    snap, findings = run_with(files)
    assert findings == []
    text = json.dumps(snap)
    assert "192.168" not in text and "shareSMBEnabled" not in text
    assert snap["settings"]["network"] == {"BRIDGING": "yes"}
    # A share that happens to be called docker is still a share
    snap, _ = run_with([("shares/docker.cfg", b'shareUseCache="yes"\n')])
    assert snap["shares"][0]["name"] == "docker" and snap["settings"] == {}


def test_hard_link_support_turned_off():
    _, findings = run_with(GOOD + [cfg("share.cfg", fuse_useino="no")])
    assert titles(findings) == ["Unraid's hard link support is turned off"]
    assert findings[0]["severity"] == "problem"


def test_appdata_through_fuse_suggests_exclusive_shares():
    _, findings = run_with(GOOD + [cfg("share.cfg", shareUserExclusive="no")])
    assert titles(findings) == ["The appdata share goes through Unraid's slower FUSE layer"]
    assert findings[0]["items"] == ["radarr", "sonarr"]


def test_docker_storage():
    docker = dict(DOCKER_ENABLED="yes", DOCKER_IMAGE_TYPE="folder", DOCKER_IMAGE_FILE="/mnt/user/appdata/docker/")
    _, findings = run_with(GOOD + [cfg("docker.cfg", **docker), cfg("share.cfg", shareUserExclusive="no")])
    assert "Docker's folder goes through Unraid's FUSE layer" in titles(findings)
    _, findings = run_with(GOOD + [cfg("docker.cfg", **{**docker, "DOCKER_IMAGE_FILE": "/mnt/user/data/docker.img",
                                                       "DOCKER_IMAGE_TYPE": ""})])
    assert titles(findings) == ["Docker's image file (docker.img) can end up on the array"]
    _, findings = run_with(GOOD + [cfg("docker.cfg", **{**docker, "DOCKER_IMAGE_FILE": "/mnt/disk1/system/docker/"})])
    assert titles(findings) == ["Docker's folder can end up on the array"]


def test_macvlan_with_bridging_on_older_unraid():
    files = [template("pihole", "pihole/pihole")]
    files[0] = (files[0][0], files[0][1].replace(b"<Network>bridge</Network>", b"<Network>br0</Network>"))
    files += [cfg("docker.cfg", DOCKER_NETWORK_TYPE=""), cfg("network.cfg", BRIDGING="yes")]
    _, findings = run_with(files, version='version="6.12.6"')
    assert titles(findings) == ["Containers with their own IP address can crash this Unraid version"]
    # Fixed in later versions, and not guessed at without one
    assert run_with(files, version="7.1.4")[1] == []
    assert run_with(files)[1] == []


MODERATION = {
    "https://raw.githubusercontent.com/dmacias72/unRAID-speedtest/master/plugin/speedtest.plg":
        {"Blacklist": True, "ModeratorComment": "This plugin appears to be <b>completely</b> broken"},
    "https://raw.githubusercontent.com/hugenbd/ca.mover.tuning/master/plugins/ca.mover.tuning.plg":
        {"Deprecated": True, "ModeratorComment": "Recommended to install the version from masterwishx"},
    "dalongrong/cloudbeaver:latest": {"Deprecated": True},
    "linuxserver/sonarr": {"ModeratorComment": "Just a note"},
}


def test_deprecated_and_blacklisted_plugins_and_containers():
    files = GOOD + [
        plugin("speedtest.plg", "https://raw.githubusercontent.com/dmacias72/unRAID-speedtest/master/plugin/speedtest.plg"),
        plugin("ca.mover.tuning.plg", "https://raw.githubusercontent.com/hugenbd/ca.mover.tuning/master/plugins/ca.mover.tuning.plg"),
        plugin("fix.common.problems.plg", "https://raw.githubusercontent.com/unraid/fix.common.problems/master/plugins/fix.common.problems.plg"),
        plugin("appdata.backup.plg", "https://raw.githubusercontent.com/Commifreak/unraid-appdata.backup/master/appdata.backup.plg"),
        template("cloudbeaver", "dalongrong/cloudbeaver"),
    ]
    snap, findings = run_with(files, moderation=MODERATION)
    assert "secret-script" not in json.dumps(snap)
    by_title = {f["title"]: f for f in findings}
    assert by_title["The speedtest.plg plugin is blacklisted in Community Applications"]["severity"] == "problem"
    assert "completely broken" in by_title["The speedtest.plg plugin is blacklisted in Community Applications"]["detail"]
    assert "masterwishx" in by_title["The ca.mover.tuning.plg plugin is deprecated in Community Applications"]["detail"]
    assert "The cloudbeaver container is deprecated in Community Applications" in by_title
    assert len(findings) == 3
    # Without the list, nothing is guessed
    assert run_with(files)[1] == []


def test_recommended_plugins_only_when_plugins_were_sent():
    assert run_with(GOOD)[1] == []
    _, findings = run_with(GOOD + [plugin("community.applications.plg", "https://example.com/ca.plg")])
    assert sorted(titles(findings)) == ["The Appdata Backup plugin isn't installed",
                                        "The Fix Common Problems plugin isn't installed"]


HAVE_PLUGINS = [plugin("fix.common.problems.plg", "https://example.com/fcp.plg"),
                plugin("appdata.backup.plg", "https://example.com/ab.plg")]
LSPCI = "0600:8086:4660 0300:8086:4680 0200:10ec:8125 0c03:8086:7ae0"


def test_hardware_ids_are_the_only_thing_kept_from_lspci():
    snap, _ = run_with(GOOD, hardware="0300:8086:4680 Intel Corporation; rm -rf / 0200:10ec:8125")
    assert snap["hardware"] == [{"class": "0300", "vendor": "8086", "device": "4680"},
                                {"class": "0200", "vendor": "10ec", "device": "8125"}]


def test_intel_gpu_not_given_to_plex():
    files = GOOD + HAVE_PLUGINS + [gpu_template("plex", "lscr.io/linuxserver/plex"),
                                   gpu_template("jellyfin", "jellyfin/jellyfin", devices=["/dev/dri"])]
    _, findings = run_with(files, hardware=LSPCI)
    assert sorted(titles(findings)) == ["Intel GPU TOP isn't installed", "Plex can't use your GPU for transcoding",
                                        "Your Realtek RTL8125 network card uses Unraid's built-in driver"]
    plex = next(f for f in findings if f["app"] == "Plex")
    assert "/dev/dri" in plex["steps"][0] and "Plex Pass" in plex["steps"][-1]


def test_nvidia_gpu_needs_the_driver_and_runtime():
    files = GOOD + HAVE_PLUGINS + [
        gpu_template("plex", "plexinc/pms-docker", extra="--runtime=nvidia", variables=[("NVIDIA_VISIBLE_DEVICES", "GPU-1")]),
        gpu_template("jellyfin", "jellyfin/jellyfin", extra="--runtime=nvidia")]
    _, findings = run_with(files, hardware="0300:10de:2204")
    assert sorted(titles(findings)) == ["Jellyfin can't use your GPU for transcoding", "Your Nvidia GPU has no driver"]
    # No GPU, or a server-board's own graphics, means nothing to say
    assert run_with(files, hardware="0300:1a03:2000 0600:8086:4660")[1] == []
    # Not sent at all (chosen by hand): not guessed at
    assert run_with(files)[1] == []


def test_compose_gpus():
    compose = (b"services:\n  plex:\n    image: plexinc/pms-docker\n    devices:\n      - /dev/dri:/dev/dri\n"
               b"  jellyfin:\n    image: jellyfin/jellyfin\n    deploy:\n      resources:\n        reservations:\n"
               b"          devices:\n            - driver: nvidia\n              count: all\n              capabilities: [gpu]\n")
    snap, _ = run_with([("compose.manager/projects/media/docker-compose.yml", compose)])
    gpus = {c["name"]: c["gpu"] for c in snap["containers"]}
    assert gpus == {"plex": {"dri": True, "nvidia": False}, "jellyfin": {"dri": False, "nvidia": True}}


def test_moderation_list_is_downloaded_once_a_day(monkeypatch):
    import os
    from conftest import REAL_MODERATION
    from core import runner

    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {**MODERATION, "some/app": {"Video": "https://youtube.com/x"}}

    monkeypatch.setattr(runner.requests, "get", lambda url, timeout: calls.append(url) or Response())
    if os.path.exists(runner.MODERATION_FILE):
        os.remove(runner.MODERATION_FILE)
    entries = REAL_MODERATION()
    assert "some/app" not in entries and "linuxserver/sonarr" not in entries and len(entries) == 3
    assert REAL_MODERATION() == entries and len(calls) == 1

    def offline(url, timeout):
        raise runner.requests.ConnectionError("offline")

    monkeypatch.setattr(runner.requests, "get", offline)
    os.remove(runner.MODERATION_FILE)
    assert REAL_MODERATION() is None
