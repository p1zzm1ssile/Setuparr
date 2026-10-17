"""Running the checks, keeping the results, and carrying out fixes.

Results, the Unraid snapshot and the fix history live as JSON in CONFIG_DIR.
A fix is only ever carried out from the server's own stored copy of a
finding: the browser sends nothing but the finding's id.
"""

import hmac
import json
import os
import secrets
import threading
import time

import requests

import app_info
import utils.config as config
from checks import SEVERITY_ORDER, anime, arr, downloads, library, tags, unraid
from services.connections import ServiceError, connected
from utils.logger import logger

RESULTS_FILE = os.path.join(config.config_dir, "results.json")
SNAPSHOT_FILE = os.path.join(config.config_dir, "unraid.json")
FIXES_FILE = os.path.join(config.config_dir, "fixes.json")
MODERATION_FILE = os.path.join(config.config_dir, "moderation.json")
MODERATION_SECONDS = 24 * 3600
# Dropped into the config folder from Unraid's terminal, imported, then deleted
IMPORT_NAMES = ("unraid-config.tar.gz", "unraid-config.tgz", "unraid-config.zip", "unraid-config.tar")

_lock = threading.Lock()
_running = threading.Event()


def _read(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    os.makedirs(config.config_dir, exist_ok=True)
    tmp = path + ".tmp"
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(data, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def results():
    data = _read(RESULTS_FILE, {"ran_at": None, "findings": [], "errors": []})
    data["running"] = _running.is_set()
    dismissed = set(_read(FIXES_FILE, {}).get("dismissed", []))
    for f in data["findings"]:
        f["dismissed"] = f["id"] in dismissed
    return data


# The Unraid snapshot


def snapshot():
    return _read(SNAPSHOT_FILE, None)


def import_files(files, hardware=None, version=None):
    """[(name, bytes)] from an upload or the config folder, and what the terminal command adds: lspci -n's PCI IDs
    and the Unraid version. Returns a summary."""
    snap = unraid.build_snapshot(unraid.read_upload(files), hardware, version)
    old = snapshot() or {}
    snap["ignored"] = [n for n in old.get("ignored", []) if any(c["name"] == n for c in snap["containers"])]
    snap["imported_at"] = time.time()
    _write(SNAPSHOT_FILE, snap)
    logger.log("ACTION", f"Imported Unraid settings: {len(snap['containers'])} containers, {len(snap['shares'])} shares")
    return unraid.summary(snap)


def leftover_file():
    """An archive from an earlier version's instructions, still sitting in the config folder."""
    return next((n for n in IMPORT_NAMES if os.path.isfile(os.path.join(config.config_dir, n))), None)


def delete_leftover():
    name = leftover_file()
    if name:
        os.remove(os.path.join(config.config_dir, name))
        logger.info(f"Deleted {name} from the config folder")


def moderation():
    """Community Applications' list of deprecated and blacklisted plugins and containers, downloaded at most once a
    day. Only the entries the checks use are kept. None if it has never been downloaded and can't be now."""
    cached = _read(MODERATION_FILE, None)
    if cached and time.time() - cached.get("fetched_at", 0) < MODERATION_SECONDS:
        return cached["entries"]
    try:
        r = requests.get(unraid.MODERATION_URL, timeout=20)
        r.raise_for_status()
        entries = {k: {f: v[f] for f in ("Blacklist", "Deprecated", "ModeratorComment") if f in v}
                   for k, v in r.json().items() if isinstance(v, dict) and (v.get("Blacklist") or v.get("Deprecated"))}
        _write(MODERATION_FILE, {"fetched_at": time.time(), "entries": entries})
        return entries
    except (requests.RequestException, OSError, ValueError, AttributeError) as e:
        logger.warning(f"Couldn't download Community Applications' moderation list | {e}")
        return cached["entries"] if cached else None


# One-time links for sending the files straight from Unraid's terminal, so nothing is written to disk
PUSH_SECONDS = 15 * 60
_push_tokens = {}


def new_push_token():
    now = time.time()
    for token, expires in list(_push_tokens.items()):
        if expires < now:
            _push_tokens.pop(token, None)
    token = secrets.token_urlsafe(24)
    _push_tokens[token] = now + PUSH_SECONDS
    return token


def use_push_token(token):
    """True once for a valid token, which then stops working."""
    for known, expires in list(_push_tokens.items()):
        if hmac.compare_digest(known, token or "") and expires >= time.time():
            _push_tokens.pop(known, None)
            return True
    return False


def import_from_config_folder():
    """Import an archive left in the config folder, then delete it, since it can hold passwords."""
    for name in IMPORT_NAMES:
        path = os.path.join(config.config_dir, name)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                data = f.read()
            summary = import_files([(name, data)])
            os.remove(path)
            logger.info(f"Deleted {name} from the config folder after importing it")
            return summary
    raise unraid.UploadError(f"There's no {IMPORT_NAMES[0]} in Setuparr's config folder yet")


def set_ignored(names):
    snap = snapshot()
    if not snap:
        raise ValueError("Nothing has been imported yet")
    known = {c["name"] for c in snap["containers"]}
    snap["ignored"] = sorted(n for n in names if n in known)
    _write(SNAPSHOT_FILE, snap)


def forget_snapshot():
    if os.path.exists(SNAPSHOT_FILE):
        os.remove(SNAPSHOT_FILE)


# Fetching from the apps


def fetch_arr(arr, kind):
    data = {
        "tags": arr.call("GET", "/tag/detail"),
        "indexers": arr.call("GET", "/indexer"),
        "clients": arr.call("GET", "/downloadclient"),
        "items": arr.call("GET", "/series" if kind == "sonarr" else "/movie"),
        "roots": arr.call("GET", "/rootfolder"),
        "profiles": arr.call("GET", "/qualityprofile"),
        "naming": arr.call("GET", "/config/naming"),
    }
    # Only some checks need these, and Sonarr v3 has no custom formats
    for key, path in (("media", "/config/mediamanagement"), ("handling", "/config/downloadclient"),
                      ("formats", "/customformat"), ("health", "/health"), ("host", "/config/host")):
        try:
            data[key] = arr.call("GET", path)
        except ServiceError:
            data[key] = None
    return data


def fetch_client(client):
    return {"label": client.label, "settings": client.settings()}


def fetch_prowlarr(p):
    return {"tags": p.call("GET", "/tag/detail"), "indexers": p.call("GET", "/indexer"),
            "proxies": p.call("GET", "/indexerproxy"), "apps": p.call("GET", "/applications")}


def fetch_seerr(s):
    servers = []
    for kind in ("sonarr", "radarr"):
        for settings in s.call("GET", f"/settings/{kind}") or []:
            try:
                live = (s.call("GET", f"/service/{kind}/{settings['id']}") or {}).get("tags")
            except ServiceError:
                live = None  # The server may be down; its tags just aren't checked
            servers.append({"kind": kind, "settings": settings, "live_tags": live})
    return {"servers": servers}


def run():
    """Run every check that has what it needs. Safe to call from several threads."""
    if not _lock.acquire(blocking=False):
        return False
    _running.set()
    try:
        findings, errors = [], []
        services = connected(config)
        fetched = {}
        fetchers = {"sonarr": lambda s: fetch_arr(s, "sonarr"), "radarr": lambda s: fetch_arr(s, "radarr"),
                    "prowlarr": fetch_prowlarr, "seerr": fetch_seerr,
                    **{key: fetch_client for key in app_info.LOGINS}}
        for key, fetch in fetchers.items():
            if key in services:
                try:
                    fetched[key] = fetch(services[key])
                except ServiceError as e:
                    logger.warning(f"Couldn't read from {key}: {e}")
                    errors.append(str(e))

        def run_check(name, fn, *args):
            try:
                findings.extend(fn(*args))
            except Exception as e:  # A bug in one check shouldn't hide the others
                logger.error(f"The {name} check failed | {e}")
                errors.append(f"The {name} check failed: {e}")

        snap = snapshot()
        if snap:
            run_check("Unraid", unraid.check, snap,
                      moderation() if snap.get("plugins") or snap.get("containers") else None)
        clients = {key: fetched[key] for key in app_info.LOGINS if key in fetched}
        for key, label in (("sonarr", "Sonarr"), ("radarr", "Radarr")):
            if key in fetched:
                run_check(f"{label} tags", tags.check_arr, key, label, fetched[key])
                run_check(f"{label} library", library.check, key, label, fetched[key])
                run_check(f"{label} profiles and health", arr.check, key, label, fetched[key], snap)
                run_check(f"{label} download clients", downloads.check_arr, key, label, fetched[key], clients)
        run_check("shared categories", downloads.check_shared_categories,
                  {key: (label, fetched[key]) for key, label in (("sonarr", "Sonarr"), ("radarr", "Radarr")) if key in fetched})
        for key, client in clients.items():
            run_check(client["label"], downloads.check_client, key, client["label"], client["settings"], snap)
        if "prowlarr" in fetched:
            run_check("Prowlarr tags", tags.check_prowlarr, fetched["prowlarr"])
        if "seerr" in fetched:
            run_check("Seerr tags", tags.check_seerr, fetched["seerr"])
        if "sonarr" in fetched:
            run_check("anime", anime.check, fetched["sonarr"], fetched.get("seerr"))

        findings.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], f["area"], f["title"]))
        _write(RESULTS_FILE, {"ran_at": time.time(), "findings": findings, "errors": errors,
                              "checked": {"unraid": bool(snap), **{k: k in fetched for k in fetchers}}})
        logger.info(f"Checks finished: {len(findings)} found" + (f", {len(errors)} couldn't run" if errors else ""))
        return True
    finally:
        _running.clear()
        _lock.release()


def run_in_background():
    threading.Thread(target=run, daemon=True).start()


# Fixes


def _history():
    return _read(FIXES_FILE, {"fixes": [], "dismissed": []})


def _service(key):
    service = connected(config).get(key)
    if not service:
        raise ValueError(f"{key.capitalize()} isn't connected any more. Check Settings")
    return service


def apply_fix(finding_id):
    found = next((f for f in _read(RESULTS_FILE, {}).get("findings", []) if f["id"] == finding_id), None)
    if not found or not found.get("fix"):
        raise ValueError("That fix isn't available any more. Run the checks again")
    action, params = found["fix"]["action"], found["fix"]["params"]

    if action == "delete_tag":
        service = _service(params["service"])
        tag = service.call("GET", f"/tag/{params['tag_id']}")
        detail = next((t for t in service.call("GET", "/tag/detail") if t["id"] == params["tag_id"]), None)
        if detail and tags.linked(detail):
            raise ValueError(f"The tag \"{tag['label']}\" is in use now, so it wasn't deleted. Run the checks again")
        service.call("DELETE", f"/tag/{params['tag_id']}")
        undo = {"action": "create_tag", "params": {"service": params["service"], "label": tag["label"]}}
        message = f"Deleted the tag \"{tag['label']}\" from {service.label}"

    elif action == "set_series_type":
        service = _service("sonarr")
        before = {}
        for series_id in params["series_ids"]:
            series = service.call("GET", f"/series/{series_id}")
            before[str(series_id)] = series.get("seriesType")
            series["seriesType"] = params["type"]
            service.call("PUT", f"/series/{series_id}", series)
        undo = {"action": "restore_series_type", "params": {"types": before}}
        message = f"Set {len(before)} series to {params['type'].capitalize()} in Sonarr"
    else:
        raise ValueError("Unknown fix")

    history = _history()
    history["fixes"].insert(0, {"id": f"{int(time.time() * 1000)}", "at": time.time(), "title": found["title"],
                                "message": message, "undo": undo, "undone": False})
    history["fixes"] = history["fixes"][:200]
    _write(FIXES_FILE, history)
    logger.log("ACTION", message)
    run_in_background()
    return message


def undo_fix(fix_id):
    history = _history()
    entry = next((f for f in history["fixes"] if f["id"] == fix_id), None)
    if not entry or entry["undone"]:
        raise ValueError("That can't be undone")
    action, params = entry["undo"]["action"], entry["undo"]["params"]
    if action == "create_tag":
        service = _service(params["service"])
        service.call("POST", "/tag", {"label": params["label"]})
        message = f"Added the tag \"{params['label']}\" back to {service.label}"
    elif action == "restore_series_type":
        service = _service("sonarr")
        for series_id, old in params["types"].items():
            series = service.call("GET", f"/series/{series_id}")
            series["seriesType"] = old
            service.call("PUT", f"/series/{series_id}", series)
        message = f"Put {len(params['types'])} series back to their old series type in Sonarr"
    else:
        raise ValueError("Unknown undo")
    entry["undone"] = True
    _write(FIXES_FILE, history)
    logger.log("ACTION", f"Undo: {message}")
    run_in_background()
    return message


def fixes():
    return [{k: v for k, v in f.items() if k != "undo"} for f in _history()["fixes"]]


def set_dismissed(finding_id, dismissed):
    history = _history()
    ids = set(history.get("dismissed", []))
    (ids.add if dismissed else ids.discard)(finding_id)
    history["dismissed"] = sorted(ids)
    _write(FIXES_FILE, history)
