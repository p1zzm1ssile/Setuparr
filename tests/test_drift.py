import copy

import pytest

from checks import drift
from core import runner
from services import trash
from test_trash import GUIDE as BASE, FakeArr

GUIDE = {**BASE,
         "quality-size": [{"trash_id": "s1", "type": "movie", "qualities": [
             {"quality": "WEBDL-1080p", "min": 12.5, "preferred": 1999, "max": 2000},
             {"quality": "HDTV-1080p", "min": 15, "preferred": 1999, "max": 2000}]},
             {"trash_id": "s2", "type": "anime", "qualities": [{"quality": "WEBDL-1080p", "min": 1, "max": 5}]}],
         "naming": {"folder": {"default": "{Movie CleanTitle} ({Release Year})",
                               "jellyfin-tmdb": "{Movie CleanTitle} ({Release Year}) [tmdbid-{TmdbId}]"},
                    "file": {"standard": "{Movie CleanTitle} {(Release Year)} {-Release Group}",
                             "jellyfin-tmdb": "{Movie CleanTitle} {(Release Year)} [tmdbid-{TmdbId}] {-Release Group}",
                             "jellyfin-anime-tmdb": "anime"}}}


def imported():
    """A Radarr with the WEB-1080p profile freshly imported from the guide."""
    arr = FakeArr()
    trash.apply("radarr", arr, "p1")
    profile = next(iter(arr.profiles.values()))
    return arr, {"profiles": [profile], "formats": list(arr.formats.values()), "items": [{"qualityProfileId": profile["id"]}]}


@pytest.fixture(autouse=True)
def guide(monkeypatch):
    monkeypatch.setattr(trash, "guide", lambda app, fresh=False: GUIDE)


def test_a_freshly_imported_profile_matches():
    _, d = imported()
    assert drift.check_profiles("radarr", "Radarr", d, GUIDE) == []


def test_a_changed_score_a_missing_format_and_settings_are_listed():
    _, d = imported()
    profile = d["profiles"][0]
    repack = next(f for f in d["formats"] if f["name"] == "Repack")
    for f in profile["formatItems"]:
        if f["format"] == repack["id"]:
            f["score"] = 0
    d["formats"] = [f for f in d["formats"] if f["name"] != "BR-DISK"]
    profile["minFormatScore"] = 50
    profile["upgradeAllowed"] = False
    [found] = drift.check_profiles("radarr", "Radarr", d, GUIDE)
    assert found["severity"] == "warning"  # a missing custom format, not only a different score
    assert found["items"] == sorted([
        "Custom format BR-DISK is missing (TRaSH scores it -10000)",
        "Custom format Repack scores 0 here, 5 in TRaSH",
        "Minimum Custom Format Score is 50 here, 0 in TRaSH",
        "Upgrades Allowed is off here, on in TRaSH"])
    assert found["fix"]["action"] == "trash_import" and found["fix"]["params"] == {"app": "radarr", "trash_id": "p1"}


def test_profiles_with_their_own_names_and_sonarr_v3_are_left_alone():
    _, d = imported()
    d["profiles"][0]["name"] = "My WEB"
    d["profiles"][0]["minFormatScore"] = 50
    assert drift.check_profiles("radarr", "Radarr", d, GUIDE) == []
    assert drift.check_profiles("radarr", "Radarr", {**d, "formats": None}, GUIDE) == []


def test_update_to_trash_fixes_the_profile_and_can_be_undone(monkeypatch):
    arr, d = imported()
    profile = d["profiles"][0]
    before = copy.deepcopy(profile)
    profile["minFormatScore"] = 50
    arr.profiles[profile["id"]] = profile
    [found] = drift.check_profiles("radarr", "Radarr", d, GUIDE)
    runner._write(runner.RESULTS_FILE, {"ran_at": 1, "findings": [found], "errors": []})
    monkeypatch.setattr(runner, "_service", lambda key: arr)
    monkeypatch.setattr(runner, "run_in_background", lambda: None)
    assert runner.apply_fix(found["id"]).startswith('Updated the TRaSH profile "WEB-1080p"')
    assert arr.profiles[profile["id"]]["minFormatScore"] == 0
    runner.undo_fix(runner.fixes()[0]["id"])
    assert arr.profiles[profile["id"]]["minFormatScore"] == 50
    assert before["minFormatScore"] == 0


DEFINITIONS = [
    {"id": 1, "quality": {"id": 3, "name": "WEBDL-1080p"}, "minSize": 2, "preferredSize": 1999, "maxSize": 2000},
    {"id": 2, "quality": {"id": 2, "name": "HDTV-1080p"}, "minSize": 15, "preferredSize": None, "maxSize": None},
    {"id": 3, "quality": {"id": 0, "name": "Unknown"}, "minSize": 1, "preferredSize": 5, "maxSize": 10},
]


def test_quality_sizes_are_only_compared_when_trash_profiles_are_used():
    _, d = imported()
    found = drift.check("radarr", "Radarr", {**d, "definitions": DEFINITIONS}, GUIDE)
    [sizes] = [f for f in found if f["title"].endswith("differs from TRaSH's")]
    # Unlimited (empty) values and qualities TRaSH doesn't size aren't counted
    assert sizes["items"] == ["WEBDL-1080p: Minimum 2 here, 12.5 in TRaSH"]
    d["profiles"][0]["name"] = "My WEB"
    assert not [f for f in drift.check("radarr", "Radarr", {**d, "definitions": DEFINITIONS}, GUIDE)
                if "quality size" in f["title"]]


class SizesArr:
    label = "Radarr"

    def __init__(self):
        self.definitions = copy.deepcopy(DEFINITIONS)

    def call(self, method, path, body=None):
        assert path in ("/qualitydefinition", "/qualitydefinition/update")
        if method == "PUT":
            self.definitions = copy.deepcopy(body)
        return copy.deepcopy(self.definitions)


def test_use_trash_sizes_and_undo(monkeypatch):
    arr = SizesArr()
    [found] = drift.check_sizes("radarr", "Radarr", arr.definitions, GUIDE)
    runner._write(runner.RESULTS_FILE, {"ran_at": 1, "findings": [found], "errors": []})
    monkeypatch.setattr(runner, "_service", lambda key: arr)
    monkeypatch.setattr(runner, "run_in_background", lambda: None)
    assert runner.apply_fix(found["id"]) == "Set 1 quality size in Radarr to TRaSH's"
    assert arr.definitions[0]["minSize"] == 12.5
    assert arr.definitions[1]["maxSize"] is None and arr.definitions[2] == DEFINITIONS[2]
    assert drift.check_sizes("radarr", "Radarr", arr.definitions, GUIDE) == []
    runner.undo_fix(runner.fixes()[0]["id"])
    assert arr.definitions == DEFINITIONS


def test_naming_suggests_the_connected_media_servers_version():
    naming = {"renameMovies": True, "standardMovieFormat": "{Movie Title}",
              "movieFolderFormat": "{Movie CleanTitle}   ({Release Year})"}
    [found] = drift.check_naming("radarr", "Radarr", naming, GUIDE, ["jellyfin"])
    # Spacing doesn't count, so the folder format matches
    assert found["items"] == ["Standard Movie Format"]
    assert "Standard Movie Format: {Movie CleanTitle} {(Release Year)} [tmdbid-{TmdbId}] {-Release Group}" in found["steps"]
    [found] = drift.check_naming("radarr", "Radarr", naming, GUIDE)
    assert "Standard Movie Format: {Movie CleanTitle} {(Release Year)} {-Release Group}" in found["steps"]


def test_any_of_trashs_naming_versions_counts_and_renaming_off_is_left_to_its_own_check():
    naming = {"renameMovies": True, "standardMovieFormat": GUIDE["naming"]["file"]["jellyfin-anime-tmdb"],
              "movieFolderFormat": GUIDE["naming"]["folder"]["default"]}
    assert drift.check_naming("radarr", "Radarr", naming, GUIDE) == []
    assert drift.check_naming("radarr", "Radarr", {**naming, "renameMovies": False, "standardMovieFormat": "x"},
                              GUIDE) == []


def test_without_the_guide_nothing_is_compared():
    _, d = imported()
    assert drift.check("radarr", "Radarr", d, None) == []
