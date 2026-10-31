"""One-click TRaSH quality profiles for Sonarr and Radarr.

The guide's profiles, custom format groups and custom formats are downloaded from the TRaSH-Guides repository on
GitHub (public files, nothing about your setup is sent) and kept for a day in the config folder. Importing a profile
adds or updates the custom formats it uses, then adds the profile, or updates the one with the same name. Everything
it changes is remembered so it can be undone.

The custom formats picked for a profile are the ones TRaSH turns on for it by default: the profile's own, plus each
custom format group that's on by default and meant for the profile, with that group's required and default formats.
Optional groups are left for you to add by hand.
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import app_info
import utils.config as config
from services.connections import ServiceError
from utils.logger import logger

APPS = {"sonarr": "Sonarr", "radarr": "Radarr"}
RAW = "https://raw.githubusercontent.com/TRaSH-Guides/Guides/master/docs/json/{app}/{path}"
LISTING = "https://api.github.com/repos/TRaSH-Guides/Guides/contents/docs/json/{app}/{folder}?ref=master"
FOLDERS = ("quality-profiles", "cf-groups", "cf")
CACHE_SECONDS = 24 * 3600
# Quality groups made by the import get ids from here, like Sonarr and Radarr's own
GROUP_IDS = 1000


class TrashError(ValueError):
    pass


def _cache_file(app):
    return os.path.join(config.config_dir, f"trash-{app}.json")


def _get(url):
    r = requests.get(url, timeout=30, headers={"User-Agent": app_info.NAME, "Accept": "application/json"})
    r.raise_for_status()
    return r.json()


def download(app):
    """Every quality profile, custom format group and custom format in the guide for app, plus its profile groups."""
    data = {"groups": _get(RAW.format(app=app, path="quality-profile-groups/groups.json"))}
    with ThreadPoolExecutor(8) as pool:
        for folder in FOLDERS:
            names = [e["name"] for e in _get(LISTING.format(app=app, folder=folder))
                     if e.get("type") == "file" and str(e.get("name", "")).endswith(".json")]
            data[folder] = list(pool.map(lambda n, folder=folder: _get(RAW.format(app=app, path=f"{folder}/{n}")), names))
    return data


def guide(app, fresh=False):
    """The guide's files for app, downloaded at most once a day. An old copy is used if GitHub can't be reached."""
    if app not in APPS:
        raise TrashError("Profiles can only be imported into Sonarr or Radarr")
    path = _cache_file(app)
    try:
        with open(path) as f:
            cached = json.load(f)
    except (OSError, ValueError):
        cached = None
    if cached and not fresh and time.time() - cached.get("fetched_at", 0) < CACHE_SECONDS:
        return cached["data"]
    try:
        data = download(app)
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        if cached:
            logger.warning(f"Couldn't download the TRaSH guides for {APPS[app]}, using the copy from before | {e}")
            return cached["data"]
        raise TrashError(f"Couldn't download the TRaSH guides from GitHub ({e.__class__.__name__}). Try again later")
    os.makedirs(config.config_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"fetched_at": time.time(), "data": data}, f)
    os.replace(tmp, path)
    logger.info(f"Downloaded the TRaSH guides for {APPS[app]}: {len(data['quality-profiles'])} profiles, "
                f"{len(data['cf'])} custom formats")
    return data


def plain(html):
    """TRaSH's descriptions are HTML with <br> between lines."""
    text = re.sub(r"<br\s*/?>", "\n", str(html or ""), flags=re.I)
    return re.sub(r"<[^>]+>", "", text).strip()


def catalogue(g):
    """The profiles to choose from, in TRaSH's own groups (Standard, Anime, French...)."""
    profiles = {p["trash_id"]: p for p in g["quality-profiles"] if p.get("visible") != "private"}
    out = []
    for group in g["groups"]:
        if str(group.get("name", "")).upper() == "TEST":  # TRaSH's own template, not meant to be used
            continue
        found = [profiles[t] for t in (group.get("profiles") or {}).values() if t in profiles]
        if found:
            out.append({"name": group.get("name") or "Other", "profiles": [
                {"id": p["trash_id"], "name": p["name"], "description": plain(p.get("trash_description")),
                 "url": p.get("trash_url")} for p in found]})
    return out


def find_profile(g, trash_id):
    profile = next((p for p in g["quality-profiles"] if p["trash_id"] == trash_id), None)
    if not profile:
        raise TrashError("That profile isn't in the TRaSH guides any more. Reload the page")
    return profile


def is_on(value):
    return value is True or str(value).lower() == "true"


def selected_formats(g, profile):
    """[(custom format, score)] for the profile: its own formats, then the groups TRaSH turns on by default for it."""
    formats = {c["trash_id"]: c for c in g["cf"]}
    ids = list((profile.get("formatItems") or {}).values())
    for group in g["cf-groups"]:
        meant_for = ((group.get("quality_profiles") or {}).get("include") or {}).values()
        if is_on(group.get("default")) and profile["trash_id"] in meant_for:
            ids += [c["trash_id"] for c in group.get("custom_formats") or []
                    if is_on(c.get("required")) or is_on(c.get("default"))]
    score_set = profile.get("trash_score_set") or "default"
    out, seen = [], set()
    for trash_id in ids:
        if trash_id in seen or trash_id not in formats:
            continue
        seen.add(trash_id)
        scores = formats[trash_id].get("trash_scores") or {}
        out.append((formats[trash_id], scores.get(score_set, scores.get("default", 0))))
    return out


def format_body(cf):
    """A TRaSH custom format as Sonarr and Radarr's API wants it: each specification's fields as a list."""
    return {
        "name": cf["name"],
        "includeCustomFormatWhenRenaming": bool(cf.get("includeCustomFormatWhenRenaming")),
        "specifications": [{
            "name": s["name"], "implementation": s["implementation"], "negate": bool(s.get("negate")),
            "required": bool(s.get("required")),
            "fields": [{"name": k, "value": v} for k, v in (s.get("fields") or {}).items()],
        } for s in cf.get("specifications") or []],
    }


def same_format(existing, body):
    """Whether a custom format already matches TRaSH's. Sonarr and Radarr return every field of a specification,
    so only the fields TRaSH sets are compared."""
    if bool(existing.get("includeCustomFormatWhenRenaming")) != body["includeCustomFormatWhenRenaming"]:
        return False
    theirs = {(s["name"], s["implementation"]): s for s in existing.get("specifications") or []}
    if len(theirs) != len(body["specifications"]):
        return False
    for s in body["specifications"]:
        other = theirs.get((s["name"], s["implementation"]))
        if not other or bool(other.get("negate")) != s["negate"] or bool(other.get("required")) != s["required"]:
            return False
        values = {f.get("name"): f.get("value") for f in other.get("fields") or []}
        if any(values.get(f["name"]) != f["value"] for f in s["fields"]):
            return False
    return True


def quality_items(trash_items, definitions):
    """TRaSH lists qualities best first, as names. Sonarr and Radarr want them worst first, with their own quality
    objects, and every quality they know listed once: the ones TRaSH leaves out are added, not allowed."""
    qualities = {d["quality"]["name"]: d["quality"] for d in definitions}
    used, items, next_id = set(), [], GROUP_IDS
    for item in reversed(trash_items):
        allowed = bool(item.get("allowed"))
        if item.get("items"):
            members = [n for n in item["items"] if n in qualities and n not in used]
            if not members:
                continue
            used.update(members)
            items.append({"id": next_id, "name": item["name"], "allowed": allowed,
                          "items": [{"quality": qualities[n], "items": [], "allowed": allowed} for n in members]})
            next_id += 1
        elif item["name"] in qualities and item["name"] not in used:
            used.add(item["name"])
            items.append({"quality": qualities[item["name"]], "items": [], "allowed": allowed})
    missing = [{"quality": q, "items": [], "allowed": False} for n, q in qualities.items() if n not in used]
    return missing + items


def cutoff_id(items, name):
    for item in items:
        if item.get("name") == name and "id" in item:
            return item["id"]
        if "quality" in item and item["quality"]["name"] == name:
            return item["quality"]["id"]
    raise TrashError(f"The profile's cutoff, {name}, isn't one of this app's qualities. Is it up to date?")


def profile_body(profile, definitions, formats, scores, languages=None, existing=None):
    """The quality profile to send. scores: {custom format id: score} for the formats this import sets. Other
    formats keep the score they have in an existing profile, or get 0 in a new one."""
    items = quality_items(profile.get("items") or [], definitions)
    old = {f["format"]: f.get("score", 0) for f in (existing or {}).get("formatItems") or []}
    body = {
        **(existing or {}),
        "name": profile["name"],
        "upgradeAllowed": bool(profile.get("upgradeAllowed", True)),
        "cutoff": cutoff_id(items, profile.get("cutoff")),
        "items": items,
        "minFormatScore": profile.get("minFormatScore", 0),
        "cutoffFormatScore": profile.get("cutoffFormatScore", 0),
        "minUpgradeFormatScore": profile.get("minUpgradeFormatScore", 1),
        "formatItems": [{"format": f["id"], "name": f["name"], "score": scores.get(f["id"], old.get(f["id"], 0))}
                        for f in formats],
    }
    if languages is not None and profile.get("language"):
        language = next((l for l in languages if str(l.get("name")).lower() == profile["language"].lower()), None)
        if language:
            body["language"] = {"id": language["id"], "name": language["name"]}
    return body


def plan(app, service, trash_id):
    g = guide(app)
    profile = find_profile(g, trash_id)
    existing_formats = {str(c.get("name", "")).lower(): c for c in service.call("GET", "/customformat") or []}
    existing_profile = next((p for p in service.call("GET", "/qualityprofile") or []
                             if str(p.get("name", "")).lower() == profile["name"].lower()), None)
    formats = []
    for cf, score in selected_formats(g, profile):
        body = format_body(cf)
        old = existing_formats.get(cf["name"].lower())
        action = "add" if not old else "same" if same_format(old, body) else "update"
        formats.append({"body": body, "score": score, "action": action, "existing": old})
    return {"profile": profile, "formats": formats, "existing_profile": existing_profile}


def preview(app, service, trash_id):
    """What importing the profile would change, for the confirm step."""
    p = plan(app, service, trash_id)
    return {
        "name": p["profile"]["name"],
        "url": p["profile"].get("trash_url"),
        "description": plain(p["profile"].get("trash_description")),
        "profile": "update" if p["existing_profile"] else "add",
        "formats": [{"name": f["body"]["name"], "score": f["score"], "action": f["action"]} for f in p["formats"]],
    }


def apply(app, service, trash_id):
    """Import the profile. Returns (message, undo). If something fails part-way, what was already changed is put
    back before the error is raised."""
    p = plan(app, service, trash_id)
    undo = {"app": app, "profile": None, "created_formats": [], "updated_formats": []}
    try:
        scores = {}
        for f in p["formats"]:
            old = f["existing"]
            if f["action"] == "add":
                new = service.call("POST", "/customformat", f["body"])
                undo["created_formats"].append(new["id"])
                scores[new["id"]] = f["score"]
            else:
                if f["action"] == "update":
                    service.call("PUT", f"/customformat/{old['id']}", {**f["body"], "id": old["id"]})
                    undo["updated_formats"].append(old)
                scores[old["id"]] = f["score"]
        languages = service.call("GET", "/language") if p["profile"].get("language") else None
        body = profile_body(p["profile"], service.call("GET", "/qualitydefinition"),
                            service.call("GET", "/customformat"), scores, languages, p["existing_profile"])
        if p["existing_profile"]:
            service.call("PUT", f"/qualityprofile/{p['existing_profile']['id']}", body)
            undo["profile"] = {"previous": p["existing_profile"]}
        else:
            new = service.call("POST", "/qualityprofile", body)
            undo["profile"] = {"created": new["id"], "name": new.get("name") or body["name"]}
    except (ServiceError, KeyError, TypeError) as e:
        try:
            put_back(service, undo)
        except ServiceError as again:
            logger.error(f"Couldn't put everything back after a failed import | {again}")
        if isinstance(e, ServiceError):
            raise
        raise TrashError(f"{service.label} answered in a way Setuparr didn't expect, so nothing was changed")

    counts = []
    for action, word in (("add", "added"), ("update", "updated")):
        n = sum(1 for f in p["formats"] if f["action"] == action)
        if n:
            counts.append(f"{word} {n} custom format{'s' if n != 1 else ''}")
    message = (f"{'Updated' if p['existing_profile'] else 'Added'} the TRaSH profile \"{p['profile']['name']}\" in "
               f"{service.label}" + (f" and {' and '.join(counts)}" if counts else ""))
    return message, undo


def put_back(service, undo):
    """Undo an import: the profile first, since custom formats in use by it can't be deleted."""
    profile = undo.get("profile") or {}
    if "created" in profile:
        service.call("DELETE", f"/qualityprofile/{profile['created']}")
    elif "previous" in profile:
        service.call("PUT", f"/qualityprofile/{profile['previous']['id']}", profile["previous"])
    for old in undo.get("updated_formats") or []:
        service.call("PUT", f"/customformat/{old['id']}", old)
    for format_id in undo.get("created_formats") or []:
        service.call("DELETE", f"/customformat/{format_id}")
