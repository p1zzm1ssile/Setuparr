import copy

import pytest

from core import runner


class FakeSonarr:
    """Enough of Sonarr's API for the fixes."""

    label = "Sonarr"

    def __init__(self):
        self.tags = {1: {"id": 1, "label": "old"}, 2: {"id": 2, "label": "anime"}}
        self.used = {2}
        self.series = {4: {"id": 4, "title": "One Piece", "seriesType": "standard"}}

    def call(self, method, path, body=None):
        parts = path.strip("/").split("/")
        if parts[0] == "tag":
            if parts[1:] == ["detail"]:
                return [{**t, "seriesIds": [9] if i in self.used else []} for i, t in self.tags.items()]
            if method == "POST":
                new = {"id": max(self.tags) + 1, "label": body["label"]}
                self.tags[new["id"]] = new
                return new
            if method == "DELETE":
                return self.tags.pop(int(parts[1]))
            return copy.deepcopy(self.tags[int(parts[1])])
        if parts[0] == "series":
            if method == "PUT":
                self.series[int(parts[1])] = copy.deepcopy(body)
                return body
            return copy.deepcopy(self.series[int(parts[1])])
        raise AssertionError(path)


@pytest.fixture
def sonarr(monkeypatch):
    fake = FakeSonarr()
    monkeypatch.setattr(runner, "connected", lambda cfg: {"sonarr": fake})
    monkeypatch.setattr(runner, "run_in_background", lambda: None)
    return fake


def store(findings):
    runner._write(runner.RESULTS_FILE, {"ran_at": 1, "findings": findings, "errors": []})


def delete_finding(tag_id):
    return {"id": f"del{tag_id}", "title": "unused", "fix": {"action": "delete_tag", "params": {
        "service": "sonarr", "tag_id": tag_id, "label": "x"}}}


def test_delete_a_tag_and_undo(sonarr):
    store([delete_finding(1)])
    assert runner.apply_fix("del1") == 'Deleted the tag "old" from Sonarr'
    assert 1 not in sonarr.tags
    fix = runner.fixes()[0]
    assert "undo" not in fix  # never sent to the browser
    assert runner.undo_fix(fix["id"]) == 'Added the tag "old" back to Sonarr'
    assert "old" in [t["label"] for t in sonarr.tags.values()]
    with pytest.raises(ValueError):
        runner.undo_fix(fix["id"])


def test_a_tag_that_is_in_use_now_is_not_deleted(sonarr):
    store([delete_finding(2)])
    with pytest.raises(ValueError, match="in use now"):
        runner.apply_fix("del2")
    assert 2 in sonarr.tags


def test_set_series_type_and_undo(sonarr):
    store([{"id": "type", "title": "anime", "fix": {"action": "set_series_type",
                                                    "params": {"series_ids": [4], "type": "anime"}}}])
    runner.apply_fix("type")
    assert sonarr.series[4]["seriesType"] == "anime"
    runner.undo_fix(runner.fixes()[0]["id"])
    assert sonarr.series[4]["seriesType"] == "standard"


def test_only_stored_findings_can_be_fixed(sonarr):
    store([])
    with pytest.raises(ValueError, match="isn't available"):
        runner.apply_fix("del1")
