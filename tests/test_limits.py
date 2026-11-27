import copy

import pytest

import utils.config as config
from checks import drift, limits
from core import runner
from test_drift import GUIDE


def q(qid, name, resolution, allowed=True):
    return {"quality": {"id": qid, "name": name, "resolution": resolution}, "items": [], "allowed": allowed}


PROFILE = {
    "id": 1, "name": "Any", "cutoff": 1001,
    "items": [
        q(1, "SDTV", 480),
        q(3, "WEBDL-1080p", 1080),
        {"id": 1001, "name": "WEB 2160p", "allowed": True, "items": [
            {**q(18, "WEBDL-2160p", 2160)}, {**q(17, "WEBRip-2160p", 2160)}]},
        q(19, "Bluray-2160p", 2160, allowed=False),
    ],
}
ONLY_4K = {"id": 2, "name": "4K only", "cutoff": 18,
           "items": [q(3, "WEBDL-1080p", 1080, allowed=False), q(18, "WEBDL-2160p", 2160)]}


def library(**extra):
    return {"profiles": [copy.deepcopy(PROFILE), copy.deepcopy(ONLY_4K)],
            "items": [{"title": "Dune", "qualityProfileId": 1, "runtime": 155},
                      {"title": "Heat", "qualityProfileId": 2, "runtime": 170}], **extra}


def test_no_limits_means_no_findings():
    d = library(definitions=[{"quality": {"name": "WEBDL-1080p"}, "maxSize": None}])
    assert limits.check("radarr", "Radarr", d) == []


def test_profiles_allowing_more_than_the_highest_resolution_are_listed():
    [found] = limits.check_profiles("radarr", "Radarr", library(), 1080)
    assert found["title"] == "2 profiles in Radarr can grab films above 1080p"
    assert found["items"] == ["4K only: WEBDL-2160p", "Any: WEB 2160p"]
    # A profile with nothing left at 1080p can't be fixed with the button
    assert found["fix"]["params"] == {"service": "radarr", "max_resolution": 1080, "profile_ids": [1]}
    assert "4K only only allows qualities above 1080p" in found["detail"]
    assert limits.check_profiles("radarr", "Radarr", library(), 2160) == []


def test_capping_a_profile_moves_upgrade_until_down():
    capped = limits.capped_profile(PROFILE, 1080)
    group = capped["items"][2]
    assert not group["allowed"] and not any(i["allowed"] for i in group["items"])
    assert capped["cutoff"] == 3
    assert PROFILE["items"][2]["allowed"]  # the original is left alone
    assert limits.capped_profile(ONLY_4K, 1080) is None


class ProfilesArr:
    label = "Radarr"

    def __init__(self):
        self.profiles = {1: copy.deepcopy(PROFILE)}

    def call(self, method, path, body=None):
        profile_id = int(path.rsplit("/", 1)[1])
        if method == "PUT":
            self.profiles[profile_id] = copy.deepcopy(body)
        return copy.deepcopy(self.profiles[profile_id])


@pytest.fixture
def fixing(monkeypatch):
    def use(arr, found):
        runner._write(runner.RESULTS_FILE, {"ran_at": 1, "findings": [found], "errors": []})
        monkeypatch.setattr(runner, "_service", lambda key: arr)
        monkeypatch.setattr(runner, "run_in_background", lambda: None)
    return use


def test_turn_off_above_the_limit_and_undo(fixing):
    arr = ProfilesArr()
    [found] = limits.check_profiles("radarr", "Radarr", library(), 1080)
    fixing(arr, found)
    assert runner.apply_fix(found["id"]) == "Turned off the qualities above 1080p in Any in Radarr"
    assert arr.profiles[1]["cutoff"] == 3 and not arr.profiles[1]["items"][2]["allowed"]
    runner.undo_fix(runner.fixes()[0]["id"])
    assert arr.profiles[1] == PROFILE


DEFINITIONS = [
    {"id": 1, "quality": {"id": 3, "name": "WEBDL-1080p"}, "minSize": 2, "preferredSize": 30, "maxSize": 100},
    {"id": 2, "quality": {"id": 18, "name": "WEBDL-2160p"}, "minSize": 25, "preferredSize": None, "maxSize": None},
    {"id": 3, "quality": {"id": 1, "name": "SDTV"}, "minSize": 1, "preferredSize": 5, "maxSize": 10},
]


def test_the_size_limit_is_worked_out_from_how_long_things_run():
    # 90% of the library fits in the runtime used, and there's a sensible guess for an empty library
    items = [{"runtime": r} for r in (90, 100, 110, 120, 130, 140, 150, 160, 170, 240)]
    assert limits.typical_runtime("radarr", items) == 240
    assert limits.typical_runtime("radarr", items * 2) == 240
    assert limits.typical_runtime("radarr", items[:9] + [{"runtime": 0}]) == 170
    assert limits.typical_runtime("sonarr", []) == 45
    assert limits.mb_per_minute("radarr", {"items": [{"runtime": 128}]}, 10) == 80
    assert limits.mb_per_minute("radarr", {"items": []}, 0) is None


def test_sizes_above_the_limit_and_keep_to_my_limit(fixing):
    [found] = limits.check_sizes("radarr", "Radarr", DEFINITIONS, 50)
    assert found["items"] == ["WEBDL-1080p: 100 MB per minute", "WEBDL-2160p: unlimited"]
    arr = SizesArr()
    fixing(arr, found)
    assert runner.apply_fix(found["id"]) == "Brought 2 quality sizes in Radarr down to 50 MB per minute"
    assert arr.definitions[0] == {**DEFINITIONS[0], "maxSize": 50}
    assert arr.definitions[1] == {**DEFINITIONS[1], "preferredSize": 50, "maxSize": 50}
    assert arr.definitions[2] == DEFINITIONS[2]
    assert limits.check_sizes("radarr", "Radarr", arr.definitions, 50) == []
    runner.undo_fix(runner.fixes()[0]["id"])
    assert arr.definitions == DEFINITIONS


class SizesArr:
    label = "Radarr"

    def __init__(self):
        self.definitions = copy.deepcopy(DEFINITIONS)

    def call(self, method, path, body=None):
        if method == "PUT":
            self.definitions = copy.deepcopy(body)
        return copy.deepcopy(self.definitions)


def test_trash_sizes_are_compared_within_the_limit():
    definitions = [{"quality": {"name": "WEBDL-1080p"}, "minSize": 12.5, "preferredSize": 50, "maxSize": 50}]
    [found] = drift.check_sizes("radarr", "Radarr", definitions, GUIDE)
    assert found["items"] == ["WEBDL-1080p: Preferred 50 here, 1999 in TRaSH, Maximum 50 here, 2000 in TRaSH"]
    assert drift.check_sizes("radarr", "Radarr", definitions, GUIDE, cap=50) == []
    [found] = drift.check_sizes("radarr", "Radarr", definitions, GUIDE, cap=60)
    assert found["items"] == ["WEBDL-1080p: Preferred 50 here, 60 in your limit, Maximum 50 here, 60 in your limit"]
    assert found["fix"]["params"]["cap"] == 60


def test_films_and_series_already_over_the_limits():
    films = [{"title": "Dune", "movieFile": {"size": 60 * 1024 ** 3, "quality": {"quality": {"resolution": 2160}}}},
             {"title": "Heat", "movieFile": {"size": 8 * 1024 ** 3, "quality": {"quality": {"resolution": 1080}}}},
             {"title": "Alien"}]
    high, big = limits.check_library("radarr", "Radarr", films, 1080, 40)
    assert high["title"] == "1 film in Radarr is above 1080p" and high["items"] == ["Dune (4K)"]
    assert big["title"] == "1 film in Radarr is over 40 GB" and big["items"] == ["Dune (60.0 GB)"]
    series = [{"title": "Severance", "statistics": {"episodeFileCount": 10, "sizeOnDisk": 50 * 1024 ** 3}},
              {"title": "Taskmaster", "statistics": {"episodeFileCount": 0, "sizeOnDisk": 0}}]
    [big] = limits.check_library("sonarr", "Sonarr", series, 1080, 2)
    assert big["title"] == "1 series in Sonarr is over 2 GB an episode"
    assert big["items"] == ["Severance (5.0 GB per episode)"]


def test_the_sliders_only_take_their_own_steps():
    field = config.FIELDS_BY_KEY["sonarr_max_size_gb"]
    assert config.coerce(field, "0.5") == 0.5 and config.coerce(field, 2) == 2
    with pytest.raises(ValueError):
        config.coerce(field, 7)
    with pytest.raises(ValueError):
        config.coerce(config.FIELDS_BY_KEY["radarr_max_resolution"], "lots")
    assert config.FIELDS_BY_KEY["radarr_max_resolution"]["option_labels"][-1] == "No limit"
    assert config.radarr_max_resolution == 0 and config.sonarr_max_size_gb == 0
