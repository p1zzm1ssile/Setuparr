"""Download clients: how Sonarr and Radarr are set up to use them, and the clients' own settings.

Sonarr and Radarr's download client list needs nothing extra. The clients'
own settings (qBittorrent, Transmission, Deluge) need their connection in
Settings, and the Unraid settings let Setuparr check their folders too.
"""

from checks import PROBLEM, TIP, WARNING, finding, plural
from checks import unraid

AREA = "downloads"
TRASH_QBITTORRENT = "https://trash-guides.info/Downloaders/qBittorrent/Basic-Setup/"
TRASH_PATHS = "https://trash-guides.info/File-and-Folder-Structure/How-to-set-up/Unraid/"
TRASH_DOWNLOADERS = "https://trash-guides.info/Downloaders/"
TRASH_DELUGE_LABELS = "https://trash-guides.info/Downloaders/Deluge/Using-Labels/"
CATEGORY_FIELD = {"sonarr": "tvCategory", "radarr": "movieCategory"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
CACHE_FIRST = ("yes", "only", "prefer")  # Unraid share settings that put new files on a cache pool


def field(client, name):
    return next((f.get("value") for f in client.get("fields") or [] if f.get("name") == name), None)


def check_arr(service, label, d, clients=None):
    """Sonarr or Radarr's download clients. d holds clients and handling (config/downloadclient)."""
    findings = []
    clients = clients or {}
    active = [c for c in d["clients"] if c.get("enable")]

    if (d.get("handling") or {}).get("enableCompletedDownloadHandling") is False:
        findings.append(finding(
            "handling", AREA, PROBLEM, label,
            f"Completed Download Handling is off in {label}",
            f"{label} doesn't import anything your download clients finish, so downloads pile up and never reach "
            "your library.",
            steps=[f"In {label}, go to Settings > Download Clients and turn on Completed Download Handling."],
            link=TRASH_DOWNLOADERS))

    local = [c for c in active if str(field(c, "host") or "").strip().lower() in LOCAL_HOSTS]
    if local:
        findings.append(finding(
            "localhost", AREA, WARNING, label,
            f"{label} reaches {plural(len(local), 'download client')} at localhost",
            f"Inside a container, localhost is the container itself, not your server. Unless {label} shares its "
            "network with the client (for example both go through the same VPN container), it can't reach it.",
            steps=[f"In {label}, go to Settings > Download Clients and use your server's IP, or the client's container "
                   "name on a custom Docker network, as the host."],
            items=[f"{c['name']}: {field(c, 'host')}" for c in local], link=TRASH_DOWNLOADERS))

    key = CATEGORY_FIELD[service]
    uncategorised = [c for c in active if any(f.get("name") == key for f in c.get("fields") or [])
                     and not str(field(c, key) or "").strip()]
    if uncategorised:
        findings.append(finding(
            "no-category", AREA, WARNING, label,
            f"{plural(len(uncategorised), 'download client')} in {label} {'has' if len(uncategorised) == 1 else 'have'} "
            "no category",
            f"Without a category, {label}'s downloads are mixed in with everything else in the client. {label} then "
            "can't tell its own downloads apart, and they can't go to their own folder.",
            steps=[f"In {label}, open each download client below and set a category, e.g. "
                   f"{'tv-sonarr' if service == 'sonarr' else 'radarr'}."],
            items=[c["name"] for c in uncategorised], link=TRASH_DOWNLOADERS))

    deluge = clients.get("deluge")
    if deluge and deluge["settings"].get("labels") is False:
        labelled = [c for c in active if c.get("implementation") == "Deluge" and str(field(c, key) or "").strip()]
        if labelled:
            findings.append(finding(
                "deluge-label", AREA, PROBLEM, label,
                f"{label} uses a category in Deluge, but Deluge's Label plugin is off",
                f"Deluge only knows categories through its Label plugin. With it off, {label} can't add downloads "
                "to Deluge.",
                steps=["In Deluge, go to Preferences > Plugins and turn on Label."],
                items=[c["name"] for c in labelled], link=TRASH_DELUGE_LABELS))
    return findings


def check_shared_categories(arrs):
    """Sonarr and Radarr sending downloads to the same client with the same category. arrs: {service: (label, d)}."""
    seen = {}
    for service, (label, d) in arrs.items():
        for c in d["clients"]:
            category = str(field(c, CATEGORY_FIELD[service]) or "").strip()
            if c.get("enable") and category:
                where = (c.get("implementation"), str(field(c, "host") or "").lower(), field(c, "port"), category)
                seen.setdefault(where, []).append(f"{label}: {c['name']}")
    findings = []
    for (app, host, port, category), users in seen.items():
        if len({u.split(":")[0] for u in users}) > 1:
            findings.append(finding(
                f"shared-category|{app}|{host}|{port}|{category}", AREA, PROBLEM, app or "Download client",
                f"Sonarr and Radarr use the same category, {category}, in {app}",
                "Each one sees the other's downloads as its own, tries to import them, and fails or puts them in "
                "the wrong place.",
                steps=["Give each its own category, e.g. tv-sonarr in Sonarr and radarr in Radarr."],
                items=users, link=TRASH_DOWNLOADERS))
    return findings


def container_for(snapshot, label):
    """The one Unraid container running this client, with its data mappings, or None."""
    if not snapshot:
        return None
    ignored = set(snapshot.get("ignored", []))
    found = [c for c in snapshot["containers"] if c["name"] not in ignored and unraid.role(c) == ("client", label)]
    if len(found) != 1:
        return None
    return {**found[0], "data": unraid.data_mounts(found[0])}


def mapping_for(container, path):
    """The data mapping a path inside the container falls under, or None."""
    path = unraid.clean(path)
    best = None
    for m in container["data"]:
        if unraid.inside(path, m["container"]) and (best is None or len(m["container"]) > len(best["container"])):
            best = m
    return best


def share_for(snapshot, mapping):
    """The Unraid share settings for the share a data mapping is in, or None."""
    where = unraid.location(mapping["host"]) if mapping else None
    if not where:
        return None
    return next((sh for sh in snapshot.get("shares", []) if sh["name"] == where[1]), None)


def check_client(service, label, s, snapshot=None):
    """One client's own settings, from services.clients."""
    findings = []
    container = container_for(snapshot, label)
    if container:
        folders = [("Default save folder", s["save_path"])]
        if s.get("incomplete_path"):
            folders.append(("Unfinished downloads", s["incomplete_path"]))
        folders += [(f"Category {name}", path) for name, path in sorted(s["categories"].items()) if path]
        lost = [(what, path) for what, path in folders if path and not mapping_for(container, path)]
        if lost:
            findings.append(finding(
                "inside-container", AREA, PROBLEM, label,
                f"{label} saves downloads to a folder that isn't mapped to your shares",
                f"These folders aren't in any of {container['name']}'s folder mappings, so downloads are written "
                "inside the container. They fill up Docker's disk image, Sonarr and Radarr can't see them, and they're "
                "lost when the container is updated.",
                steps=[f"Map /mnt/user/data to /data in {container['name']}.",
                       f"In {label}, save downloads to /data/torrents, with a folder per category under it."],
                items=[f"{what}: {path}" for what, path in lost], link=TRASH_PATHS))

        save, unfinished = mapping_for(container, s["save_path"]), s.get("incomplete_path")
        if save and unfinished and mapping_for(container, unfinished) not in (None, save):
            findings.append(finding(
                "incomplete-mapping", AREA, WARNING, label,
                f"{label} copies every finished download to another folder mapping",
                f"Unfinished downloads wait in {unfinished}, which is a different folder mapping from {s['save_path']}. "
                "Docker treats each mapping as its own drive, so every finished download is copied in full instead of "
                "moved instantly.",
                steps=[f"In {label}, turn off the separate folder for unfinished downloads, or put it inside the same "
                       "mapping as the save folder, e.g. /data/torrents/incomplete."],
                items=[f"Save folder: {s['save_path']}", f"Unfinished downloads: {unfinished}"], link=TRASH_PATHS))

        share = share_for(snapshot, save)
        if s.get("preallocate") and share and share.get("shareUseCache") in CACHE_FIRST:
            findings.append(finding(
                "preallocate", AREA, WARNING, label,
                f"{label} reserves disk space on your cache for every download",
                f"Pre-allocate disk space for all files is on, and downloads go to the {share['name']} share, which "
                f"uses your cache. {label} keeps that space reserved until it's closed, so the cache can fill up and "
                "the mover can't free it. The TRaSH guide turns this off on Unraid with a cache.",
                steps=[f"In {label}, go to Tools > Options > Downloads and turn off Pre-allocate disk space for all files."],
                link=TRASH_QBITTORRENT))

        unraid.sent(snapshot, findings)

    if s.get("automatic") is False and s["categories"]:
        findings.append(finding(
            "manual-mode", AREA, TIP, label,
            f"{label}'s categories don't choose where downloads go",
            "Default Torrent Management Mode is Manual, so every download goes to the default save folder whatever "
            "its category. The TRaSH guide uses Automatic, so each category keeps its downloads in its own folder.",
            steps=[f"In {label}, go to Tools > Options > Downloads and set Default Torrent Management Mode to Automatic."],
            items=sorted(s["categories"]), link=TRASH_QBITTORRENT))

    if s.get("no_login_networks"):
        findings.append(finding(
            "no-login", AREA, TIP, label,
            f"{label} lets some networks in without a password",
            f"Anyone on these networks can control {label} and add downloads. That's usually your own network, so "
            "this may be on purpose, but a wide range or one shared with others is risky.",
            steps=[f"In {label}, go to Tools > Options > Web UI and narrow or turn off \"Bypass authentication for "
                   "clients in whitelisted IP subnets\"."],
            items=s["no_login_networks"], link=TRASH_QBITTORRENT))
    return findings
