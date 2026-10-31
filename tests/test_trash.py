import pytest

from services import trash
from services.connections import ServiceError

GUIDE = {
    "groups": [{"name": "Standard", "profiles": {"web-1080p": "p1", "hidden": "p2"}}],
    "quality-profiles": [
        {"trash_id": "p1", "name": "WEB-1080p", "trash_description": "Line one<br>Line two", "trash_url": "https://x",
         "upgradeAllowed": True, "cutoff": "WEB 1080p", "minFormatScore": 0, "cutoffFormatScore": 10000,
         "minUpgradeFormatScore": 1, "language": "Original",
         "items": [{"name": "WEB 1080p", "allowed": True, "items": ["WEBRip-1080p", "WEBDL-1080p"]},
                   {"name": "HDTV-1080p", "allowed": True}, {"name": "SDTV", "allowed": False}],
         "formatItems": {"Repack": "cf1"}},
        {"trash_id": "p2", "name": "Private", "visible": "private", "items": [], "cutoff": "SDTV"},
    ],
    "cf-groups": [
        {"name": "Unwanted", "default": "true", "quality_profiles": {"include": {"WEB-1080p": "p1"}},
         "custom_formats": [{"name": "BR-DISK", "trash_id": "cf2", "required": False, "default": True},
                            {"name": "Optional", "trash_id": "cf3", "required": False}]},
        {"name": "Extras", "quality_profiles": {"include": {"WEB-1080p": "p1"}},
         "custom_formats": [{"name": "HDR", "trash_id": "cf4", "required": True}]},
    ],
    "cf": [
        {"trash_id": "cf1", "name": "Repack", "trash_scores": {"default": 5}, "specifications": [
            {"name": "Repack", "implementation": "ReleaseTitleSpecification", "negate": False, "required": True,
             "fields": {"value": "\\bREPACK\\b"}}]},
        {"trash_id": "cf2", "name": "BR-DISK", "trash_scores": {"default": -10000}, "specifications": [
            {"name": "BR-DISK", "implementation": "ReleaseTitleSpecification", "negate": False, "required": True,
             "fields": {"value": "disk"}}]},
        {"trash_id": "cf3", "name": "Optional", "trash_scores": {"default": 1}, "specifications": []},
        {"trash_id": "cf4", "name": "HDR", "trash_scores": {"default": 500}, "specifications": []},
    ],
}

QUALITIES = ["Unknown", "SDTV", "HDTV-1080p", "WEBRip-1080p", "WEBDL-1080p", "Bluray-1080p"]


class FakeArr:
    label = "Radarr"

    def __init__(self, formats=(), profiles=(), fail_on=None):
        self.formats = {f["id"]: f for f in formats}
        self.profiles = {p["id"]: p for p in profiles}
        self.fail_on = fail_on
        self.calls = []
        self.next_id = 100

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        if (method, path) == self.fail_on:
            raise ServiceError("Radarr answered 400")
        if method == "GET":
            if path == "/customformat":
                return list(self.formats.values())
            if path == "/qualityprofile":
                return list(self.profiles.values())
            if path == "/qualitydefinition":
                return [{"quality": {"id": i, "name": n}} for i, n in enumerate(QUALITIES)]
            if path == "/language":
                return [{"id": -1, "name": "Any"}, {"id": -2, "name": "Original"}]
        store = self.formats if "customformat" in path else self.profiles
        if method == "POST":
            self.next_id += 1
            store[self.next_id] = {**body, "id": self.next_id}
            return store[self.next_id]
        if method == "PUT":
            store[body["id"]] = body
            return body
        if method == "DELETE":
            store.pop(int(path.rsplit("/", 1)[1]))
            return None


@pytest.fixture(autouse=True)
def guide(monkeypatch):
    monkeypatch.setattr(trash, "guide", lambda app, fresh=False: GUIDE)


def test_catalogue_hides_private_profiles_and_strips_html():
    assert trash.catalogue(GUIDE) == [{"name": "Standard", "profiles": [
        {"id": "p1", "name": "WEB-1080p", "description": "Line one\nLine two", "url": "https://x"}]}]


def test_formats_are_the_profiles_own_plus_default_groups():
    picked = [(cf["name"], score) for cf, score in trash.selected_formats(GUIDE, GUIDE["quality-profiles"][0])]
    assert picked == [("Repack", 5), ("BR-DISK", -10000)]


def test_qualities_are_reversed_and_complete():
    items = trash.quality_items(GUIDE["quality-profiles"][0]["items"],
                                [{"quality": {"id": i, "name": n}} for i, n in enumerate(QUALITIES)])
    names = [i.get("name") or i["quality"]["name"] for i in items]
    assert names == ["Unknown", "Bluray-1080p", "SDTV", "HDTV-1080p", "WEB 1080p"]
    assert [i["allowed"] for i in items] == [False, False, False, True, True]
    assert trash.cutoff_id(items, "WEB 1080p") == trash.GROUP_IDS


def test_import_adds_formats_and_profile_then_undo_removes_them():
    arr = FakeArr()
    message, undo = trash.apply("radarr", arr, "p1")
    assert message == 'Added the TRaSH profile "WEB-1080p" in Radarr and added 2 custom formats'
    profile = next(iter(arr.profiles.values()))
    assert profile["language"] == {"id": -2, "name": "Original"}
    scores = {f["name"]: f["score"] for f in profile["formatItems"]}
    assert scores == {"Repack": 5, "BR-DISK": -10000}
    spec = arr.formats[101]["specifications"][0]
    assert spec["fields"] == [{"name": "value", "value": "\\bREPACK\\b"}]
    trash.put_back(arr, undo)
    assert arr.profiles == {} and arr.formats == {}


def test_existing_profile_and_formats_are_updated_and_put_back():
    old_format = {"id": 7, "name": "repack", "includeCustomFormatWhenRenaming": False, "specifications": []}
    matching = {"id": 8, "name": "BR-DISK", "includeCustomFormatWhenRenaming": False, "specifications": [
        {"name": "BR-DISK", "implementation": "ReleaseTitleSpecification", "negate": False, "required": True,
         "fields": [{"name": "value", "value": "disk"}, {"name": "other", "value": 1}]}]}
    mine = {"id": 9, "name": "Mine", "specifications": []}
    old_profile = {"id": 3, "name": "WEB-1080p", "cutoff": 0, "items": [],
                   "formatItems": [{"format": 9, "name": "Mine", "score": 42}]}
    arr = FakeArr([old_format, matching, mine], [old_profile])
    preview = trash.preview("radarr", arr, "p1")
    assert preview["profile"] == "update"
    assert [(f["name"], f["action"]) for f in preview["formats"]] == [("Repack", "update"), ("BR-DISK", "same")]
    message, undo = trash.apply("radarr", arr, "p1")
    assert message == 'Updated the TRaSH profile "WEB-1080p" in Radarr and updated 1 custom format'
    assert {f["name"]: f["score"] for f in arr.profiles[3]["formatItems"]} == {"Repack": 5, "BR-DISK": -10000, "Mine": 42}
    assert ("PUT", "/customformat/8") not in arr.calls
    trash.put_back(arr, undo)
    assert arr.profiles[3] == old_profile and arr.formats[7] == old_format


def test_a_failed_import_puts_back_what_it_changed():
    arr = FakeArr(fail_on=("POST", "/qualityprofile"))
    with pytest.raises(ServiceError):
        trash.apply("radarr", arr, "p1")
    assert arr.formats == {} and arr.profiles == {}


def test_an_import_is_remembered_and_can_be_undone(monkeypatch):
    from core import runner
    arr = FakeArr()
    monkeypatch.setattr(runner, "_service", lambda key: arr)
    monkeypatch.setattr(runner, "run_in_background", lambda: None)
    assert runner.import_trash_profile("radarr", "p1").startswith('Added the TRaSH profile "WEB-1080p"')
    fix = runner.fixes()[0]
    assert fix["title"] == "Imported a TRaSH profile" and "undo" not in fix
    assert runner.undo_fix(fix["id"]) == 'Removed "WEB-1080p" and the custom formats it added from Radarr'
    assert arr.profiles == {} and arr.formats == {}
