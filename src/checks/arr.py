"""Sonarr and Radarr's own health, security, quality profiles and backups."""

from checks import PROBLEM, TIP, WARNING, finding, plural, unraid

AREA = "arr"
TRASH = {
    "sonarr": {"profiles": "https://trash-guides.info/Sonarr/sonarr-setup-quality-profiles/",
               "formats": "https://trash-guides.info/Sonarr/sonarr-collection-of-custom-formats/"},
    "radarr": {"profiles": "https://trash-guides.info/Radarr/radarr-setup-quality-profiles/",
               "formats": "https://trash-guides.info/Radarr/Radarr-collection-of-custom-formats/"},
}
TRASH_PERMISSIONS = "https://trash-guides.info/File-and-Folder-Structure/How-to-set-up/Unraid/"
SERVARR_AUTH = "https://wiki.servarr.com/sonarr/settings#security"
SERVARR_MEDIA = {"sonarr": "https://wiki.servarr.com/sonarr/settings#file-management",
                 "radarr": "https://wiki.servarr.com/radarr/settings#file-management"}
HEALTH_SEVERITY = {"error": PROBLEM, "warning": WARNING, "notice": TIP}
# Health checks Setuparr already explains in more detail
COVERED_HEALTH = {"RootFolderCheck"}
ITEMS = {"sonarr": ("series", "series"), "radarr": ("film", "films")}


def check(service, label, d, snapshot=None):
    """d holds what fetch_arr reads: profiles, formats, items, and health, host and media when they could be read."""
    return (check_health(label, d.get("health")) + check_security(label, d.get("host"))
            + check_permissions(label, d.get("media")) + check_recycle_bin(label, d.get("media"))
            + check_profiles(service, label, d) + check_unused_profiles(service, label, d)
            + check_availability(service, d.get("items")) + check_backups(label, d.get("host"), snapshot))


def check_health(label, health):
    findings = []
    for h in health or []:
        severity = HEALTH_SEVERITY.get(str(h.get("type", "")).lower())
        source = h.get("source") or ""
        if not severity or source in COVERED_HEALTH or not h.get("message"):
            continue
        wiki = h.get("wikiUrl") or ""
        if isinstance(wiki, dict):
            wiki = wiki.get("fullUri") or ""
        findings.append(finding(
            f"health|{source}", AREA, severity, label, f"{label}: {h['message'].rstrip('.')}",
            f"{label} shows this on its System > Status page. Until it's fixed, {label} may not search, grab or "
            "import as it should.",
            steps=[f"Open System > Status in {label}; the link next to the message explains the fix."],
            link=wiki if str(wiki).startswith(("http://", "https://")) else None))
    return findings


def check_security(label, host):
    if not host:
        return []
    method, required = host.get("authenticationMethod"), host.get("authenticationRequired")
    if method in (None, "none"):
        return [finding(
            "auth-off", AREA, WARNING, label, f"Anyone who can reach {label} can change it",
            f"{label} has no login, so anyone on your network, or anyone on the internet if it's reachable through a "
            "reverse proxy or port forward, can see and change it and read its API key, which also unlocks your "
            "indexers and download clients.",
            steps=[f"In {label}, go to Settings > General > Security, set Authentication to Forms (Login Page) and "
                   "set a username and password."],
            link=SERVARR_AUTH)]
    if method != "external" and required == "disabledForLocalAddresses":
        return [finding(
            "auth-local", AREA, TIP, label, f"{label} doesn't ask for a password on your network",
            f"Authentication Required is set to Disabled for Local Addresses. If you reach {label} through a reverse "
            "proxy (Nginx Proxy Manager, SWAG, Traefik) on the same server, every visitor looks local, so anyone "
            "from the internet gets in without a password.",
            steps=[f"If {label} is reachable from outside your home, go to Settings > General > Security and set "
                   "Authentication Required to Enabled."],
            link=SERVARR_AUTH)]
    return []


def check_permissions(label, media):
    if not media or not media.get("setPermissionsLinux"):
        return []
    return [finding(
        "set-permissions", AREA, WARNING, label, f"{label} changes file permissions itself",
        f"Set Permissions is on, so {label} runs chmod and chown on every import. On Unraid, PUID, PGID and UMASK "
        "already do this, and the two fight: other containers lose access, or imports fail with permission errors.",
        steps=[f"In {label}, go to Settings > Media Management (Show Advanced) > Permissions and turn off Set "
               "Permissions.",
               "Keep PUID 99, PGID 100 and UMASK 002 in the container instead."],
        link=TRASH_PERMISSIONS)]


def check_recycle_bin(label, media):
    if not media or "recycleBin" not in media or media.get("recycleBin"):
        return []
    return [finding(
        "recycle-bin", AREA, TIP, label, f"{label} deletes files for good",
        f"No recycle bin is set, so when {label} upgrades a file or you delete something by mistake, the old file "
        "is gone straight away.",
        steps=[f"Make a folder such as /data/media/.recycle, then in {label} go to Settings > Media Management (Show "
               "Advanced) > File Management and set Recycling Bin to it, with Recycling Bin Cleanup at 7 days."],
        link=SERVARR_MEDIA[label.lower()])]


def check_unused_profiles(service, label, d):
    if not d.get("items"):
        return []
    used = {i.get("qualityProfileId") for i in d["items"]}
    unused = sorted(p.get("name") or "?" for p in d.get("profiles") or [] if p.get("id") not in used)
    if not unused:
        return []
    return [finding(
        "unused-profiles", AREA, TIP, label,
        f"{plural(len(unused), 'quality profile')} in {label} {'isn' if len(unused) == 1 else 'aren'}'t used",
        f"No {ITEMS[service][1]} use {'it' if len(unused) == 1 else 'them'}. Spare profiles make it easy to pick the "
        "wrong one when you add something, and Recyclarr or Notifiarr keep updating them for nothing.",
        steps=[f"In {label}, go to Settings > Profiles and delete the ones you don't need."],
        items=unused, link=TRASH[service]["profiles"])]


def check_availability(service, items):
    """Radarr films still waiting to download that can be grabbed as soon as they're announced, which means cams."""
    if service != "radarr":
        return []
    early = sorted(i.get("title") or "?" for i in items or []
                   if i.get("minimumAvailability") == "announced" and i.get("monitored") and not i.get("hasFile"))
    if not early:
        return []
    return [finding(
        "announced", AREA, TIP, "Radarr", f"{plural(len(early), 'film')} can be grabbed as soon as {'it is' if len(early) == 1 else 'they are'} announced",
        "Minimum Availability is Announced, so Radarr searches before the film is out, when the only releases are "
        "cams, telesyncs and fakes. Some get grabbed if your profile allows them.",
        steps=["In Radarr, select the films (Movies > Select Movies), choose Edit and set Minimum Availability to "
               "Released.",
               "When you add a film, choose Released under Minimum Availability. Radarr remembers it for next time."],
        items=early, link=TRASH["radarr"]["profiles"])]


def allowed_ids(profile):
    """The allowed qualities and groups of a profile, lowest first, by the id its cutoff uses."""
    ids = []
    for item in profile.get("items") or []:
        if not item.get("allowed"):
            continue
        ids.append(item.get("id") if item.get("items") else (item.get("quality") or {}).get("id"))
    return [i for i in ids if i is not None]


def check_profiles(service, label, d):
    profiles = d.get("profiles") or []
    formats = d.get("formats")
    used = {}
    for item in d.get("items") or []:
        used.setdefault(item.get("qualityProfileId"), []).append(item.get("title") or "?")
    one, things = ITEMS[service]
    findings = []

    # Sonarr v3 has no custom formats (None); v4 and Radarr with none imported return []
    if formats == [] and d.get("items"):
        findings.append(finding(
            "no-formats", AREA, TIP, label, f"{label} has no custom formats",
            f"Custom formats are how the TRaSH guide makes {label} prefer good releases (the right sources, HDR, "
            "audio and release groups) and avoid bad ones (re-encodes, fake HDR, LQ groups). Without them, "
            f"{label} picks by quality name alone.",
            steps=[f"Import the TRaSH custom formats and a TRaSH quality profile, by hand or with Recyclarr or Notifiarr, "
                   f"then set your {things} to that profile."],
            link=TRASH[service]["formats"]))

    for p in profiles:
        name, items = p.get("name") or "?", used.get(p.get("id"), [])
        if not items:
            continue
        scores = [f.get("score") or 0 for f in p.get("formatItems") or []]
        positive = sum(s for s in scores if s > 0)
        minimum = p.get("minFormatScore") or 0
        if minimum > 0 and minimum > positive:
            findings.append(finding(
                f"min-score|{p.get('id')}", AREA, PROBLEM, label, f"Nothing can ever be grabbed for the {name} profile",
                f"The profile's Minimum Custom Format Score is {minimum}, but even a release matching every custom "
                f"format with a positive score only reaches {positive}. Every release is rejected, so "
                f"{plural(len(items), one, things)} on it never download.",
                steps=[f"In {label}, go to Settings > Profiles > {name} and lower Minimum Custom Format Score (the "
                       "TRaSH profiles mostly use 0), or score the custom formats you want."],
                items=items, link=TRASH[service]["profiles"]))
        if formats and scores and not any(scores):
            findings.append(finding(
                f"zero-scores|{p.get('id')}", AREA, WARNING, label, f"The {name} profile ignores your custom formats",
                f"Every custom format scores 0 in this profile, so {label} picks releases for its {things} as if you "
                "had none.",
                steps=[f"In {label}, go to Settings > Profiles > {name} and give the custom formats their TRaSH scores "
                       "(Recyclarr or Notifiarr can set them for you)."],
                items=items, link=TRASH[service]["profiles"]))
        allowed = allowed_ids(p)
        if p.get("upgradeAllowed") and len(allowed) > 1 and p.get("cutoff") == allowed[0] and not (
                (p.get("cutoffFormatScore") or 0) > 0 and positive > 0):
            findings.append(finding(
                f"cutoff|{p.get('id')}", AREA, TIP, label, f"The {name} profile never upgrades",
                "Upgrades are on, but Upgrade Until is the lowest quality the profile allows, so the first release "
                "grabbed is kept, however poor.",
                steps=[f"In {label}, go to Settings > Profiles > {name} and set Upgrade Until to the quality you "
                       "actually want."],
                items=items, link=TRASH[service]["profiles"]))
        if not p.get("upgradeAllowed") and positive > 0:
            findings.append(finding(
                f"no-upgrades|{p.get('id')}", AREA, TIP, label, f"The {name} profile never upgrades to better releases",
                "Upgrades are off, so once a release is grabbed, a better one (a higher custom format score, a repack "
                "or a better source) is never picked up. The TRaSH profiles turn upgrades on.",
                steps=[f"In {label}, go to Settings > Profiles > {name}, tick Upgrades Allowed and set Upgrade Until "
                       "and Upgrade Until Custom Format Score."],
                items=items, link=TRASH[service]["profiles"]))
    return findings


def container_for(snapshot, label):
    """The one Unraid container running this app, or None."""
    if not snapshot:
        return None
    ignored = set(snapshot.get("ignored", []))
    found = [c for c in snapshot["containers"] if c["name"] not in ignored and unraid.role(c) == ("arr", label)]
    return found[0] if len(found) == 1 else None


def check_backups(label, host, snapshot):
    if not host:
        return []
    findings = []
    interval, retention = host.get("backupInterval"), host.get("backupRetention")
    if interval == 0 or retention == 0:
        findings.append(finding(
            "backups-off", AREA, WARNING, label, f"{label} doesn't keep backups",
            f"Backup {'Interval' if interval == 0 else 'Retention'} is 0, so {label} keeps no backup of its database. "
            "If the database is damaged (a full cache pool, a power cut) you start again from nothing.",
            steps=[f"In {label}, go to Settings > General > Backups and set Interval to 7 days and Retention to 28 "
                   "days."]))
    folder = str(host.get("backupFolder") or "").strip()
    container = container_for(snapshot, label)
    if folder.startswith("/") and container:
        path = unraid.clean(folder)
        if not any(unraid.inside(path, unraid.clean(m["container"])) for m in container["mounts"]):
            findings.append(finding(
                "backup-folder", AREA, WARNING, label, f"{label}'s backups are lost when its container is updated",
                f"Backups are saved to {folder}, which isn't in any of {container['name']}'s folder mappings, so "
                "they're inside the container and disappear whenever it's updated or recreated.",
                steps=[f"In {label}, go to Settings > General > Backups and set Folder to Backups (inside /config), or "
                       f"to a folder that's mapped in {container['name']}."],
                items=[folder]))
    return findings
