"""Bazarr: its own health list, its link to Sonarr and Radarr, subtitle providers, default profiles, the folder
paths it sees (from the Unraid templates) and backups."""

from checks import PROBLEM, TIP, WARNING, finding, plural, unraid

AREA = "bazarr"
LABEL = "Bazarr"
WIKI = "https://wiki.bazarr.media/Getting-Started/Setup-Guide/"
WIKI_PATHS = "https://wiki.bazarr.media/Getting-Started/Setup-Guide/#path-mappings"
LOCAL = ("127.0.0.1", "localhost", "0.0.0.0", "::1")
ARRS = (("sonarr", "Sonarr", "series", "serie", "path_mappings"), ("radarr", "Radarr", "films", "movie", "path_mappings_movie"))


def check(d, snapshot=None, connected=()):
    """d holds Bazarr's settings, health and providers. connected: the apps Setuparr itself is connected to."""
    settings = d.get("settings") or {}
    general = settings.get("general") or {}
    findings = []

    for h in d.get("health") or []:
        what, issue = str(h.get("object") or "").strip(), str(h.get("issue") or "").strip()
        if issue:
            findings.append(finding(
                f"health|{what}", AREA, WARNING, LABEL, f"Bazarr: {what}" if what else "Bazarr has a health warning",
                f"Bazarr shows this on its System > Status page: {issue}",
                steps=["Open System > Status in Bazarr and fix what it describes."], link=WIKI))

    used = [a for a in ARRS if general.get(f"use_{a[0]}")]
    if settings and not used:
        findings.append(finding(
            "not-connected", AREA, PROBLEM, LABEL, "Bazarr isn't connected to Sonarr or Radarr",
            "Bazarr only finds your series and films through Sonarr and Radarr, so with neither turned on it gets no "
            "subtitles at all.",
            steps=["In Bazarr, go to Settings > Sonarr and Settings > Radarr, turn each on, and enter its address and "
                   "API key."],
            link=WIKI))
    for key, name, things, prefix, _ in ARRS:
        if not settings:
            break
        if not general.get(f"use_{key}"):
            if key in connected:
                findings.append(finding(
                    f"unused|{key}", AREA, WARNING, LABEL, f"Bazarr doesn't get subtitles for {name}'s {things}",
                    f"You use {name}, but Bazarr isn't connected to it, so none of its {things} get subtitles.",
                    steps=[f"In Bazarr, go to Settings > {name}, turn it on, and enter {name}'s address and API key."],
                    link=WIKI))
            continue
        address = str((settings.get(key) or {}).get("ip") or "").strip().lower()
        if address in LOCAL:
            findings.append(finding(
                f"localhost|{key}", AREA, PROBLEM, LABEL, f"Bazarr looks for {name} at {address}",
                f"Inside Bazarr's container, {address} is Bazarr itself, so it can't reach {name}.",
                steps=[f"In Bazarr, go to Settings > {name} and set the address to Unraid's IP address (or {key}, if "
                       "both containers are on the same custom Docker network)."],
                link=WIKI))
        if not general.get(f"{prefix}_default_enabled") or general.get(f"{prefix}_default_profile") in ("", None):
            findings.append(finding(
                f"default-profile|{key}", AREA, WARNING, LABEL, f"New {things} get no subtitles in Bazarr",
                f"There's no default languages profile for {things}, so each one {name} adds has no profile and Bazarr "
                "never looks for its subtitles until you set one by hand.",
                steps=[f"In Bazarr, go to Settings > Languages > Default Settings, turn on {things.capitalize()} and "
                       "pick your languages profile.",
                       f"Then use Mass Edit on the {things.capitalize()} page to give existing ones the profile."],
                link=WIKI))

    providers = d.get("providers")
    if settings and not general.get("enabled_providers"):
        findings.append(finding(
            "no-providers", AREA, PROBLEM, LABEL, "Bazarr has no subtitle providers",
            "Without providers, Bazarr has nowhere to download subtitles from.",
            steps=["In Bazarr, go to Settings > Providers and add a few, e.g. OpenSubtitles.com, Podnapisi and "
                   "Subdl (some need a free account)."],
            link=WIKI))
    elif providers:
        throttled = [p for p in providers if str(p.get("status") or "Good") != "Good"]
        if throttled and len(throttled) == len(providers):
            findings.append(finding(
                "throttled", AREA, WARNING, LABEL, "All of Bazarr's subtitle providers are paused",
                "Every provider has been throttled after errors or too many downloads, so Bazarr isn't downloading "
                "anything until they come back.",
                steps=["Open System > Providers in Bazarr to see why and when each comes back.",
                       "Add a few more providers so one being throttled doesn't stop everything."],
                items=[f"{p.get('name')}: {p.get('status')}" for p in throttled], link=WIKI))

    findings += check_paths(general, snapshot, used)

    if (settings.get("backup") or {}).get("frequency") == "Manually":
        findings.append(finding(
            "backup", AREA, TIP, LABEL, "Bazarr doesn't back itself up",
            "Backups are set to Manually, so Bazarr's database (your languages profiles and subtitle history) isn't "
            "backed up unless you press Backup.",
            steps=["In Bazarr, go to Settings > Backup and set the frequency to Weekly."]))
    return findings


def container(snapshot, test):
    if not snapshot:
        return None
    ignored = set(snapshot.get("ignored", []))
    found = [c for c in snapshot["containers"] if c["name"] not in ignored and test(c)]
    return {**found[0], "data": unraid.data_mounts(found[0])} if len(found) == 1 else None


def is_bazarr(c):
    return "bazarr" in f"{c['image']} {c['name']}".lower()


def mapped(path, mappings):
    """Where a path in Sonarr or Radarr ends up in Bazarr after Bazarr's path mappings."""
    for pair in mappings or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0] and unraid.inside(path, unraid.clean(pair[0])):
            return unraid.clean(pair[1] + path[len(unraid.clean(pair[0])):])
    return path


def check_paths(general, snapshot, used):
    """Bazarr is given Sonarr and Radarr's paths, so it has to see the media at the same paths, or have a path
    mapping for each difference."""
    bazarr = container(snapshot, is_bazarr)
    if not bazarr:
        return []
    findings = []
    for key, name, things, _, mappings_key in used:
        app = container(snapshot, lambda c: unraid.role(c) == ("arr", name))
        if not app:
            continue
        missing, different = [], []
        for m in app["data"]:
            arr_path = m["container"]
            seen = unraid.as_seen_by(bazarr["data"], m["host"])
            if seen is None:
                missing.append(f"{m['host']} ({name}: {arr_path})")
            elif mapped(arr_path, general.get(mappings_key)) != seen:
                different.append(f"{name} {arr_path} = Bazarr {seen}")
        if missing:
            findings.append(finding(
                f"paths-missing|{key}", AREA, PROBLEM, LABEL, f"Bazarr can't see {name}'s {things}",
                f"{bazarr['name']} has no folder mapping for some of the folders {name} keeps {things} in, so Bazarr "
                "can't save subtitles next to them.",
                steps=[f"Edit {bazarr['name']} and map the same folders as {app['name']}, at the same container "
                       "paths, e.g. /mnt/user/data to /data in both."],
                items=missing, link=WIKI_PATHS))
        if different:
            findings.append(finding(
                f"paths|{key}", AREA, WARNING, LABEL, f"Bazarr sees {name}'s {things} at a different path",
                f"Bazarr gets each file's path from {name}, but its container sees the folder somewhere else, and "
                f"there's no path mapping for it, so it can't find the {plural(len(different), 'folder')} below.",
                steps=[f"Best: map the folders at the same container paths in {bazarr['name']} and {app['name']}.",
                       f"Or in Bazarr, go to Settings > {name} > Path Mappings and add one for each line below."],
                items=different, link=WIKI_PATHS))
    return findings
