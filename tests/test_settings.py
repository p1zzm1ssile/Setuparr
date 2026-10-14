import json
import os

import utils.config as config


def test_a_client_password_saved_by_040_is_moved_to_its_own_setting():
    before = open(config.settings_file).read() if os.path.exists(config.settings_file) else None
    try:
        with open(config.settings_file, "w") as f:
            json.dump({"qbittorrent_url": "http://192.168.1.10:8080", "qbittorrent_api_key": "hunter2",
                       "sonarr_api_key": "abc"}, f)
        config.load()
        assert config.qbittorrent_password == "hunter2" and config.sonarr_api_key == "abc"
        saved = json.load(open(config.settings_file))
        assert saved["qbittorrent_password"] == "hunter2" and "qbittorrent_api_key" not in saved
        assert config.public_values()["qbittorrent_password"] == {"set": True}
    finally:
        if before is None:
            os.remove(config.settings_file)
        else:
            open(config.settings_file, "w").write(before)
        config.load()
