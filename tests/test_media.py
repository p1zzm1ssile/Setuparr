from checks import media


def container(name, image, gpu=False, network="bridge", mounts=()):
    return {"name": name, "image": image, "network": network, "gpu": {"dri": gpu, "nvidia": False},
            "mounts": [{"host": h, "container": c} for h, c in mounts] or [{"host": "/mnt/user/appdata/x", "container": "/config"}]}


def titles(findings):
    return sorted(f["title"] for f in findings)


GOOD_PLEX = {"HardwareAcceleratedCodecs": True, "TranscoderTempDirectory": "/transcode", "FSEventLibraryUpdatesEnabled": True,
             "LanNetworks": "192.168.1.0/24", "ButlerTaskBackupDatabase": True}


def test_a_tidy_plex_has_nothing_to_report():
    snap = {"containers": [container("plex", "plexinc/pms-docker", True, mounts=[("/tmp", "/transcode")])]}
    assert media.check("plex", {"prefs": GOOD_PLEX}, snap) == []


def test_plex_settings():
    snap = {"containers": [container("plex", "lscr.io/linuxserver/plex", True)]}
    prefs = {"HardwareAcceleratedCodecs": False, "TranscoderTempDirectory": "/transcode", "FSEventLibraryUpdatesEnabled": False,
             "LanNetworks": "", "ButlerTaskBackupDatabase": False}
    findings = media.check("plex", {"prefs": prefs}, snap)
    assert titles(findings) == ["Plex doesn't back up its database", "Plex doesn't notice new downloads straight away",
                                "Plex may treat your home devices as remote", "Plex transcodes without the GPU",
                                "Plex writes transcodes inside its container"]
    assert {f["area"] for f in findings} == {"media"}


def test_hardware_transcoding_is_only_raised_when_a_gpu_is_there():
    snap = {"containers": [container("plex", "plexinc/pms-docker", False, network="host", mounts=[("/tmp", "/transcode")])]}
    assert media.check("plex", {"prefs": {**GOOD_PLEX, "HardwareAcceleratedCodecs": False}}, snap) == []


def test_jellyfin_and_emby():
    d = {"info": {"HasPendingRestart": True},
         "encoding": {"HardwareAccelerationType": "none", "TranscodingTempPath": ""},
         "libraries": [{"name": "Films", "monitor": False}, {"name": "TV", "monitor": True}],
         "tasks": [{"name": "Scan Media Library", "status": "Failed"}, {"name": "Clean", "status": "Completed"}],
         "users": [{"name": "admin", "admin": True, "password": False}, {"name": "kids", "admin": False, "password": False},
                   {"name": "me", "admin": False, "password": True}]}
    assert titles(media.check("jellyfin", d)) == [
        "1 account in Jellyfin has no password", "1 scheduled task failed last time in Jellyfin",
        "An administrator in Jellyfin has no password", "Jellyfin doesn't notice new files in 1 library",
        "Jellyfin is waiting to be restarted", "Jellyfin transcodes without the GPU"]
    # Emby has no HardwareAccelerationType setting
    emby = {**d, "encoding": {"TranscodingTempPath": ""}}
    assert "Emby transcodes without the GPU" not in titles(media.check("emby", emby))
