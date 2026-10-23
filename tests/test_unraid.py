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
    # A server-board's own graphics needs no driver, but Plex can't start with the Nvidia runtime and no Nvidia GPU
    assert titles(run_with(files, hardware="0300:1a03:2000 0600:8086:4660")[1]) == [
        "plex is given an Nvidia GPU this server doesn't have"]
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


# Ports, start order, disks, time, alerts, the mover and backups


def port_template(name, image, ports=(), network="bridge", extra="", variables=()):
    configs = "".join(f'<Config Type="Port" Target="{c}" Mode="{m}">{h}</Config>' for h, c, m in ports)
    configs += "".join(f'<Config Type="Variable" Target="{k}">{v}</Config>' for k, v in variables)
    xml = (f'<?xml version="1.0"?><Container version="2"><Name>{name}</Name><Repository>{image}</Repository>'
           f'<Network>{network}</Network><ExtraParams>{extra}</ExtraParams>{configs}</Container>')
    return (f"plugins/dockerMan/templates-user/my-{name}.xml", xml.encode())


def ini(path, sections):
    text = "".join(f'["{name}"]\n' + "".join(f'{k}="{v}"\n' for k, v in values.items()) for name, values in sections.items())
    return (path, text.encode())


def test_ports_taken_twice_or_by_unraids_web_ui():
    files = GOOD + [
        port_template("nginx", "jc21/nginx-proxy-manager", [("80", "8080", "tcp"), ("8181", "8181", "tcp")]),
        port_template("ombi", "lscr.io/linuxserver/ombi", [("8181", "3579", "tcp"), ("8181", "3579", "udp")]),
        port_template("pihole", "pihole/pihole", [("80", "80", "tcp")], network="br0"),
        port_template("gluetun", "qmcgaw/gluetun", [("8989", "8989", "tcp")]),
    ]
    snap, findings = run_with(files)
    assert sorted(titles(findings)) == ["2 containers want port 8181", "nginx uses port 80, which Unraid's web UI needs"]
    # Containers with their own IP address don't take Unraid's ports
    assert next(c for c in snap["containers"] if c["name"] == "pihole")["ports"] == []
    # Unraid's web UI moved to another port: 80 is free
    _, findings = run_with(files + [cfg("ident.cfg", PORT="81", USE_SSL="no", NAME="Tower")])
    assert titles(findings) == ["2 containers want port 8181"]


def test_compose_ports():
    compose = (b"services:\n  a:\n    image: x/a\n    ports:\n      - '127.0.0.1:9000:80'\n      - '9001'\n"
               b"      - target: 53\n        published: 5353\n        protocol: udp\n"
               b"  b:\n    image: x/b\n    network_mode: host\n    ports: ['9000:9000']\n")
    snap, _ = run_with([("compose.manager/projects/x/compose.yaml", compose)])
    ports = {c["name"]: c["ports"] for c in snap["containers"]}
    assert ports == {"a": ["5353/udp", "9000/tcp"], "b": ["9000/tcp"]}


def test_containers_that_use_a_vpn_containers_network_start_after_it():
    files = GOOD + [port_template("gluetun", "qmcgaw/gluetun"),
                    port_template("qbit-vpn", "lscr.io/linuxserver/qbittorrent", network="container:gluetun"),
                    port_template("prowlarr", "lscr.io/linuxserver/prowlarr", extra="--net=container:gluetun")]
    autostart = ("var/lib/docker/unraid-autostart", b"qbit-vpn\nprowlarr 5\ngluetun 10\nqbittorrent\nsonarr\nradarr\n")
    snap, findings = run_with(files + [autostart])
    assert {c["name"]: c["network_of"] for c in snap["containers"]}["qbit-vpn"] == "gluetun"
    assert sorted(titles(findings)) == ["prowlarr starts before gluetun, whose network it uses",
                                        "qbit-vpn starts before gluetun, whose network it uses"]
    _, findings = run_with(files + [("unraid-autostart", b"qbit-vpn\n")])
    assert sorted(titles(findings)) == ["3 media containers don't start by themselves",
                                        "qbit-vpn starts by itself, but gluetun, whose network it uses, doesn't"]
    # Not sent: nothing guessed
    assert run_with(files)[1] == []


def test_docker_log_rotation_off():
    _, findings = run_with(GOOD + [cfg("docker.cfg", DOCKER_ENABLED="yes", DOCKER_LOG_ROTATION="no")])
    assert titles(findings) == ["Docker's log rotation is turned off"]


DISKS = {
    "parity": {"idx": "0", "name": "parity", "device": "sdb", "id": "WDC_WD80EFAX_SERIAL123", "type": "Parity",
               "status": "DISK_OK", "numErrors": "0"},
    "disk1": {"name": "disk1", "id": "ST8000_SERIAL456", "type": "Data", "status": "DISK_OK", "fsStatus": "Mounted",
              "fsSize": "7811939620", "fsFree": "1000", "numErrors": "0"},
    "cache": {"name": "cache", "id": "Samsung_SERIAL789", "type": "Cache", "status": "DISK_OK", "fsStatus": "Mounted",
              "fsSize": "1000000", "fsFree": "500000", "numErrors": "0"},
    "cache2": {"name": "cache2", "type": "Cache", "status": "DISK_OK", "fsSize": "1000000", "fsFree": "0"},
    "flash": {"name": "flash", "id": "SanDisk_SERIAL000", "type": "Flash", "status": "DISK_OK", "fsSize": "100",
              "fsFree": "50"},
}


def test_healthy_disks_have_nothing_to_report_and_serials_arent_kept():
    snap, findings = run_with(GOOD + [ini("var/local/emhttp/disks.ini", DISKS)])
    assert findings == []
    assert "SERIAL" not in json.dumps(snap) and "sdb" not in json.dumps(snap)


def test_disk_problems():
    disks = {**DISKS,
             "parity": {**DISKS["parity"], "status": "DISK_NP_DSBL"},
             "disk1": {**DISKS["disk1"], "status": "DISK_DSBL", "numErrors": "12"},
             "disk2": {"name": "disk2", "type": "Data", "status": "DISK_OK", "fsStatus": "Unmountable: Wrong or no file system"},
             "cache": {**DISKS["cache"], "fsFree": "40000"},
             "flash": {**DISKS["flash"], "fsFree": "5"}}
    _, findings = run_with(GOOD + [ini("disks.ini", disks)])
    assert sorted(titles(findings)) == [
        "1 disk had read or write errors", "Disk 1 is disabled, and its contents are being emulated from parity",
        "Disk 2 is unmountable", "The cache pool is 96% full", "The flash drive is 95% full",
        "Your array has no parity drive"]


def test_parity_checks_and_alerts():
    dynamix = ("plugins/dynamix/dynamix.cfg",
               b'[display]\ndate="%c"\n[parity]\nmode="0"\n[notify]\nalert="1"\n[ssmtp]\nAuthPass="hunter2"\n')
    snap, findings = run_with(GOOD + [ini("disks.ini", DISKS), dynamix])
    assert "hunter2" not in json.dumps(snap)
    assert sorted(titles(findings)) == ["Parity checks aren't scheduled",
                                        "Unraid only tells you about failing disks in its web UI"]
    # Monthly checks, and alerts by email (2) as well as in the browser (1)
    dynamix = ("plugins/dynamix/dynamix.cfg", b'[parity]\nmode="3"\n[notify]\nalert="3"\n')
    assert run_with(GOOD + [ini("disks.ini", DISKS), dynamix])[1] == []


def test_time():
    files = GOOD + [port_template("bazarr", "lscr.io/linuxserver/bazarr", variables=[("TZ", "America/New_York")]),
                    port_template("plex", "plexinc/pms-docker", variables=[("TZ", "Europe/London")]),
                    ("compose.manager/projects/x/compose.yaml", b"services:\n  homarr:\n    image: x/homarr\n"),
                    cfg("ident.cfg", USE_NTP="no", timeZone="Europe/London", NTP_SERVER1="192.168.1.1")]
    snap, findings = run_with(files)
    text = json.dumps(snap)
    assert "Europe/London" not in text and "New_York" not in text and "192.168" not in text
    assert sorted(titles(findings)) == ["2 containers run on a different time zone from Unraid",
                                        "Unraid's clock isn't set from the internet"]
    # Compose containers without TZ run on UTC; templates without it get Unraid's
    assert next(f["items"] for f in findings if "time zone" in f["title"]) == ["bazarr", "homarr"]


def test_appdata_shared_publicly():
    files = GOOD[:3] + [("shares/appdata.cfg", b'shareUseCache="only"\nshareExport="e"\nshareSecurity="public"\n'), GOOD[4]]
    assert titles(run_with(files)[1]) == ["Anyone on your network can read appdata"]
    files[3] = ("shares/appdata.cfg", b'shareUseCache="only"\nshareExport="e"\nshareSecurity="private"\n')
    assert run_with(files)[1] == []


def test_mover():
    files = GOOD[:4] + [("shares/data.cfg", b'shareUseCache="yes"\nshareFloor="0"\n'),
                        cfg("share.cfg", shareMoverSchedule="0 * * * *")]
    assert sorted(titles(run_with(files)[1])) == ["The data share has no minimum free space", "The mover runs every hour"]
    files = GOOD[:4] + [share("data", "only")]
    assert titles(run_with(files)[1]) == ["The data share never leaves the cache"]
    # A server with no array: the pool is meant to hold everything
    no_array = {k: v for k, v in DISKS.items() if v["type"] not in ("Data", "Parity")}
    assert run_with(files + [ini("disks.ini", no_array)])[1] == []


def backup(**config):
    return ("plugins/appdata.backup/config.json", json.dumps(config).encode())


def test_appdata_backup():
    files = GOOD + HAVE_PLUGINS
    good = dict(destination="/mnt/user/backups/appdata", backupFrequency="weekly", flashBackup="yes",
                preRunScript="/boot/secret.sh", containerSettings={"sonarr": {"skip": "no"}})
    snap, findings = run_with(files + [backup(**good)])
    assert findings == [] and "secret.sh" not in json.dumps(snap)
    _, findings = run_with(files + [backup(**{**good, "backupFrequency": "disabled", "flashBackup": "no",
                                              "destination": "/mnt/cache/backups",
                                              "containerSettings": {"sonarr": {"skip": "yes"}, "x": {"skip": "yes"}}})])
    assert sorted(titles(findings)) == ["Appdata Backup never runs by itself", "Appdata Backup skips 1 media container",
                                        "Appdata backups are on the same pool as appdata", "The flash drive isn't backed up"]
    assert titles(run_with(files + [backup(destination="")])[1]) == ["Appdata Backup has nowhere to save backups"]
    # Settings left behind by a plugin that's been removed are ignored
    _, findings = run_with(GOOD + [HAVE_PLUGINS[0], backup(destination="")])
    assert titles(findings) == ["The Appdata Backup plugin isn't installed"]


def test_plugins_made_for_another_unraid_version():
    def versioned(file, **attrs):
        extra = "".join(f' {k}="{v}"' for k, v in attrs.items())
        return (f"plugins/{file}", f'<?xml version="1.0"?><PLUGIN name="x" pluginURL="https://example.com/{file}"{extra}/>'.encode())
    files = GOOD + HAVE_PLUGINS + [versioned("old.plg", max="6.11.9"), versioned("new.plg", min="7.2.0"),
                                   versioned("fine.plg", min="6.12.0", max="7.9")]
    snap, findings = run_with(files, version="7.1.4")
    assert titles(findings) == ["2 plugins aren't made for Unraid 7.1.4"]
    assert findings[0]["items"] == ["new.plg: needs Unraid 7.2.0 or later", "old.plg: made for Unraid 6.11.9 and earlier"]
    assert run_with(files)[1] == []


def test_restart_policies_and_privileged_containers():
    files = GOOD + [port_template("gluetun", "qmcgaw/gluetun", extra="--cap-add=NET_ADMIN --restart=unless-stopped"),
                    port_template("tool", "x/tool", extra="--privileged=true -e PASSWORD=hunter2")]
    files.append((files[0][0], files[0][1].replace(b"</Container>", b"<Privileged>true</Privileged></Container>")))
    files = files[1:]
    snap, findings = run_with(files)
    assert "hunter2" not in json.dumps(snap)
    flags = {c["name"]: c["flags"] for c in snap["containers"]}
    assert flags["gluetun"] == ["--cap-add", "--restart"] and flags["tool"] == ["--privileged"]
    by = {f["title"]: f for f in findings}
    assert sorted(by) == ["1 container uses Docker's --restart instead of Unraid's Autostart",
                          "qbittorrent runs as Privileged", "tool runs as Privileged"]
    assert by["qbittorrent runs as Privileged"]["severity"] == "warning" and by["tool runs as Privileged"]["severity"] == "tip"
    assert by["tool runs as Privileged"]["link"] == "#/guides"


def run_host(files, **host):
    snap = unraid.build_snapshot(unraid.read_upload(files), host=host)
    return snap, unraid.check(json.loads(json.dumps(snap)))


def test_leftovers_from_removed_containers():
    removed = template("overseerr", "lscr.io/linuxserver/overseerr", [("/mnt/user/appdata/overseerr", "/config")])
    snap, findings = run_host(GOOD + [removed], containers="qbittorrent sonarr radarr plex ",
                              appdata="sonarr/radarr/overseerr/Plex/old-tautulli/",
                              docker="Images|2.5GB (40%);Containers|0B (0%);Local Volumes|120MB (100%);Build Cache|0B;")
    # The removed container's template isn't checked, and its folder counts as unused
    assert [c["name"] for c in snap["containers"]] == ["qbittorrent", "radarr", "sonarr"]
    assert snap["unused_templates"] == ["overseerr"]
    assert sorted(titles(findings)) == ["2 appdata folders don't belong to any container",
                                        "Docker is keeping 2.6 GB it doesn't use",
                                        "Unraid still has templates for 1 container you've removed"]
    by_title = {f["title"]: f for f in findings}
    assert by_title["2 appdata folders don't belong to any container"]["items"] == ["old-tautulli", "overseerr"]
    assert unraid.summary(snap)["unused_templates"] == 1


def test_cleanup_needs_what_the_terminal_command_sends():
    # An upload without them, a container that maps all of appdata, and a little unused space report nothing
    assert run_host(GOOD)[1] == []
    whole = template("krusader", "binhex/arch-krusader", [("/mnt/user/appdata", "/appdata")])
    assert run_host(GOOD + [whole], appdata="sonarr/radarr/old/")[1] == []
    assert run_host(GOOD, docker="Images|300MB (10%);Local Volumes|0B (0%);")[1] == []
    # docker ps failing sends nothing, so every template is still checked
    snap, _ = run_host(GOOD, containers="")
    assert len(snap["containers"]) == 3 and "unused_templates" not in snap


def test_docker_sizes():
    assert unraid.parse_size("1.5GB (30%)") == 1_500_000_000
    assert unraid.parse_size("512kB") == 512_000
    assert unraid.parse_size("0B") == 0 and unraid.parse_size("") == 0
