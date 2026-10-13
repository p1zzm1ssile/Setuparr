"""Reading Unraid's Docker templates and share settings, and checking them.

Setuparr never looks around the server itself. It reads only the files it's
given: the Docker templates (/boot/config/plugins/dockerMan/templates-user),
the share settings (/boot/config/shares) and Compose Manager's compose files.

Those files can hold VPN passwords, API keys and other personal details, so
they're read in memory and only what the checks need is kept: each container's
name, image and folder mappings, and its PUID, PGID and UMASK. Nothing else is
stored: not passwords, keys, other variables, ports, networks or time zones.
"""

import io
import os
import posixpath
import re
import tarfile
import zipfile
import xml.etree.ElementTree as ET

import yaml

from checks import PROBLEM, TIP, TRASH_HARDLINKS, TRASH_UNRAID, WARNING, finding, plural

AREA = "unraid"
KEPT_VARIABLES = {"PUID", "PGID", "UMASK", "UMASK_SET"}
MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 50 * 1024 * 1024
MAX_FILES = 5000
COMPOSE_NAMES = {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}

# What a container is, from its image name
ROLES = [
    ("arr", ("sonarr", "radarr", "lidarr", "readarr", "whisparr")),
    ("client", ("qbittorrent", "deluge", "transmission", "rtorrent", "rutorrent", "sabnzbd", "nzbget", "rdtclient")),
    ("server", ("jellyfin", "plex", "emby")),
]
APP_NAMES = {
    "sonarr": "Sonarr", "radarr": "Radarr", "lidarr": "Lidarr", "readarr": "Readarr", "whisparr": "Whisparr",
    "qbittorrent": "qBittorrent", "deluge": "Deluge", "transmission": "Transmission", "rtorrent": "rTorrent",
    "rutorrent": "rTorrent", "sabnzbd": "SABnzbd", "nzbget": "NZBGet", "rdtclient": "RDTClient",
    "jellyfin": "Jellyfin", "plex": "Plex", "emby": "Emby",
}
# Top-level folders under /mnt that aren't pools
NOT_POOLS = {"user", "user0", "remotes", "disks", "addons", "rootshare"}


class UploadError(ValueError):
    pass


# Reading the files


def read_upload(files):
    """[(name, bytes)] -> {"templates": [...], "compose": [...], "shares": [...]} raw texts, archives opened."""
    found = {"templates": [], "compose": [], "shares": []}
    total = 0

    def add(name, data):
        nonlocal total
        total += len(data)
        if total > MAX_TOTAL:
            raise UploadError("That's more than 50 MB. Only the templates, shares and compose folders are needed")
        lower = name.lower().replace("\\", "/")
        base = posixpath.basename(lower)
        if lower.endswith((".zip",)):
            return open_zip(data, add)
        if lower.endswith((".tar.gz", ".tgz", ".tar")):
            return open_tar(data, add)
        if len(data) > MAX_FILE:
            return
        if base.endswith(".xml"):
            found["templates"].append((base, data))
        elif base.endswith(".cfg") and ("/shares/" in "/" + lower or not posixpath.dirname(lower)):
            found["shares"].append((base, data))
        elif base in COMPOSE_NAMES:
            found["compose"].append((lower, data))

    for name, data in files:
        add(name, data)
    return found


def open_zip(data, add):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            members = [m for m in z.infolist() if not m.is_dir()]
            if len(members) > MAX_FILES:
                raise UploadError("The zip has too many files in it")
            for m in members:
                if m.file_size <= MAX_FILE:
                    add(m.filename, z.read(m))
    except zipfile.BadZipFile:
        raise UploadError("The zip file couldn't be opened")


def open_tar(data, add):
    try:
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            members = [m for m in t.getmembers() if m.isfile()]
            if len(members) > MAX_FILES:
                raise UploadError("The archive has too many files in it")
            for m in members:
                if m.size <= MAX_FILE:
                    add(m.name, t.extractfile(m).read())
    except tarfile.TarError:
        raise UploadError("The archive couldn't be opened")


def kept_variables(pairs):
    return {k: str(v) for k, v in pairs if k in KEPT_VARIABLES and v is not None}


def parse_template(name, data):
    """One Unraid Docker template -> a container, or None if it isn't one."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    if root.tag != "Container":
        return None
    text = lambda tag: (root.findtext(tag) or "").strip()
    mounts, variables = [], []
    for c in root.findall("Config"):
        kind, target, value = c.get("Type", ""), (c.get("Target") or "").strip(), (c.text or "").strip()
        if kind == "Path" and value and target:
            mounts.append({"host": value, "container": target, "mode": c.get("Mode", "rw")})
        elif kind == "Variable":
            variables.append((target, value))
    # Version 1 templates
    for v in root.findall("Data/Volume"):
        host, target = (v.findtext("HostDir") or "").strip(), (v.findtext("ContainerDir") or "").strip()
        if host and target:
            mounts.append({"host": host, "container": target, "mode": (v.findtext("Mode") or "rw").strip()})
    for v in root.findall("Environment/Variable"):
        variables.append(((v.findtext("Name") or "").strip(), (v.findtext("Value") or "").strip()))
    container = text("Name") or re.sub(r"^my-|\.xml$", "", name)
    return {
        "name": container,
        "source": "template",
        "image": text("Repository"),
        "mounts": mounts,
        "variables": kept_variables(variables),
    }


def parse_compose(name, data):
    try:
        doc = yaml.safe_load(data) or {}
    except yaml.YAMLError:
        return []
    containers = []
    for service, spec in (doc.get("services") or {}).items():
        if not isinstance(spec, dict):
            continue
        mounts = []
        for v in spec.get("volumes") or []:
            if isinstance(v, str):
                parts = v.split(":")
                if len(parts) >= 2 and parts[0].startswith("/"):
                    mounts.append({"host": parts[0], "container": parts[1], "mode": parts[2] if len(parts) > 2 else "rw"})
            elif isinstance(v, dict) and str(v.get("source", "")).startswith("/"):
                mounts.append({"host": v["source"], "container": v.get("target", ""),
                               "mode": "ro" if v.get("read_only") else "rw"})
        env = spec.get("environment") or {}
        pairs = env.items() if isinstance(env, dict) else [tuple(str(e).split("=", 1)) if "=" in str(e) else (str(e), None) for e in env]
        containers.append({
            "name": spec.get("container_name") or service,
            "source": "compose",
            "image": str(spec.get("image", "")),
            "mounts": mounts,
            "variables": kept_variables(pairs),
        })
    return containers


def parse_share(name, data):
    values = {}
    for line in data.decode("utf-8", "replace").splitlines():
        match = re.match(r'^\s*(\w+)\s*=\s*"?(.*?)"?\s*$', line)
        if match:
            values[match.group(1)] = match.group(2)
    keep = ("shareUseCache", "shareCachePool", "shareCachePool2", "shareAllocator", "shareSplitLevel")
    return {"name": name[:-4], **{k: values[k] for k in keep if k in values}}


def build_snapshot(raw):
    containers, seen = [], set()
    for name, data in raw["templates"]:
        c = parse_template(name, data)
        if c and c["name"] not in seen:
            seen.add(c["name"])
            containers.append(c)
    for name, data in raw["compose"]:
        for c in parse_compose(name, data):
            if c["name"] not in seen:
                seen.add(c["name"])
                containers.append(c)
    shares = [parse_share(n, d) for n, d in raw["shares"]]
    if not containers and not shares:
        raise UploadError("No Docker templates, compose files or share settings were found in what you uploaded")
    return {"containers": sorted(containers, key=lambda c: c["name"].lower()),
            "shares": sorted(shares, key=lambda s: s["name"].lower())}


# Working out what's what


def role(container):
    # lscr.io/linuxserver/sonarr:latest -> sonarr
    image = container["image"].lower().split("@")[0].rsplit("/", 1)[-1].split(":")[0]
    name = f"{image} {container['name'].lower()}"
    for kind, apps in ROLES:
        for app in apps:
            if app in name:
                return kind, APP_NAMES[app]
    return None, None


def clean(path):
    path = posixpath.normpath(path.strip())
    return "/" if path == "/" else path.rstrip("/")


def inside(path, folder):
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def data_mounts(container):
    """Folder mappings that hold media or downloads, rather than the app's own config."""
    out = []
    for m in container["mounts"]:
        host, target = clean(m["host"]), clean(m["container"])
        if not host.startswith("/mnt/") or "/appdata" in host or target in ("/config", "/transcode", "/tmp"):
            continue
        if host.split("/")[2] in ("user", "user0") and len(host.split("/")) > 3 and host.split("/")[3] == "system":
            continue
        out.append({"host": host, "container": target})
    return out


def location(host):
    """("user" or "disk", share name) for a host path under /mnt."""
    parts = host.split("/")
    if len(parts) < 4:
        return None
    top, share = parts[2], parts[3]
    if top in ("user", "user0"):
        return "user", share
    if re.match(r"disk\d+$", top) or top not in NOT_POOLS:
        return "disk", share
    return None


def as_seen_by(mounts, host):
    """The path a container sees host at, or None if it can't see it."""
    best = None
    for m in mounts:
        if inside(host, m["host"]) and (best is None or len(m["host"]) > len(best["host"])):
            best = m
    if best is None:
        return None
    return clean(best["container"] + host[len(best["host"]):])


# The checks


def check(snapshot):
    containers = [c for c in snapshot["containers"] if c["name"] not in set(snapshot.get("ignored", []))]
    for c in containers:
        c["role"], c["app"] = role(c)
        c["data"] = data_mounts(c)
    arrs = [c for c in containers if c["role"] == "arr"]
    clients = [c for c in containers if c["role"] == "client"]
    findings = []
    findings += check_downloads(arrs, clients)
    findings += check_mixed_paths(containers)
    findings += check_ids(arrs + clients + [c for c in containers if c["role"] == "server"])
    findings += check_shares(snapshot["shares"], arrs + clients)
    return findings


def check_downloads(arrs, clients):
    findings = []
    for arr in arrs:
        sees, missing, mismatched = [], [], []
        for client in clients:
            for m in client["data"]:
                if as_seen_by(arr["data"], m["host"]) is not None:
                    shared = [m["host"]]
                else:
                    # The arr may only see part of what the client's mapping holds
                    shared = [a["host"] for a in arr["data"] if inside(a["host"], m["host"])]
                if not shared:
                    missing.append(f"{client['name']} ({m['host']})")
                for host in shared:
                    sees.append(host)
                    arr_path, client_path = as_seen_by(arr["data"], host), as_seen_by(client["data"], host)
                    if arr_path != client_path:
                        mismatched.append((client, host, client_path, arr_path))

        name = f"{arr['app']} ({arr['name']})" if arr["app"].lower() != arr["name"].lower() else arr["app"]
        if clients and missing and not sees:
            findings.append(finding(
                f"no-downloads|{arr['name']}", AREA, PROBLEM, name,
                f"{name} can't see any download client's downloads",
                f"None of {name}'s folder mappings include the folders your download clients save to, so it "
                "can't import what they download.",
                steps=[
                    "Make one share called data, with torrents (or usenet) and media folders inside it.",
                    f"Map /mnt/user/data to /data in {name} and in every download client.",
                    "Set the download client to save to /data/torrents, and the root folders to /data/media/... .",
                ],
                items=missing, link=TRASH_UNRAID))

        covering = {id(a) for a in arr["data"] for h in sees if inside(h, a["host"])}
        others = [a for a in arr["data"] if id(a) not in covering]
        if covering and others:
            findings.append(finding(
                f"hardlinks|{arr['name']}", AREA, PROBLEM, name,
                f"{name} copies every download instead of hardlinking it",
                f"{name} sees downloads and your library through separate folder mappings. Docker treats each "
                "mapping as its own drive, so files can't be hardlinked or moved instantly between them: every "
                "import is a full copy, and anything still seeding takes up its space twice.",
                steps=[
                    "Put downloads and media in one share, e.g. /mnt/user/data/torrents and /mnt/user/data/media.",
                    f"Replace {name}'s separate mappings with a single one: /mnt/user/data to /data.",
                    "Do the same in your download clients, then point root folders and download paths at /data/... .",
                ],
                items=[f"{a['container']} = {a['host']}" for a in arr["data"]], link=TRASH_HARDLINKS))

        for client, host, client_path, arr_path in mismatched:
            findings.append(finding(
                f"paths|{arr['name']}|{client['name']}|{host}", AREA, WARNING, name,
                f"{client['app'] or client['name']} and {name} see downloads at different paths",
                f"{client['name']} saves downloads to {client_path}, but {name} sees that folder as {arr_path}. "
                f"{name} then needs a Remote Path Mapping to import, and imports fail if it's missing or wrong.",
                steps=[
                    f"Map the folder to the same path in both, e.g. /mnt/user/data to /data in {client['name']} and {arr['name']}.",
                    f"Or add a Remote Path Mapping in {name} (Settings > Download Clients) from {client_path} to {arr_path}.",
                ],
                items=[f"{host}: {client['name']} {client_path}, {arr['name']} {arr_path}"], link=TRASH_UNRAID))
    return findings


def check_mixed_paths(containers):
    by_share = {}
    for c in containers:
        for m in c["data"]:
            where = location(m["host"])
            if where:
                by_share.setdefault(where[1], {}).setdefault(where[0], []).append(f"{c['name']}: {m['host']}")
    findings = []
    for share, kinds in by_share.items():
        if "user" in kinds and "disk" in kinds:
            findings.append(finding(
                f"mixed|{share}", AREA, PROBLEM, "Unraid",
                f"The {share} share is mapped both as /mnt/user and as a disk or pool",
                f"Some containers reach {share} through /mnt/user/{share} and others straight through a disk or pool. "
                "Moving or copying a file between the two kinds of path can empty it, which is a known way to lose "
                "data on Unraid.",
                steps=[f"Map {share} through /mnt/user/{share} in every container."],
                items=kinds["user"] + kinds["disk"], link=TRASH_UNRAID))
    return findings


def check_ids(containers):
    findings = []
    for variable, what in (("PUID", "user"), ("PGID", "group")):
        values = {}
        for c in containers:
            if variable in c["variables"]:
                values.setdefault(c["variables"][variable], []).append(c["name"])
        if len(values) > 1:
            findings.append(finding(
                f"ids|{variable}", AREA, WARNING, "Unraid",
                f"Your media containers don't all run as the same {what} ({variable})",
                f"Files one container creates may not be readable or movable by another, which shows up as "
                "\"permission denied\" or failed imports. On Unraid it's usually PUID 99 and PGID 100.",
                steps=[f"Set {variable} to the same value in every container below, then restart them."],
                items=[f"{', '.join(sorted(n))}: {variable}={v}" for v, n in sorted(values.items())], link=TRASH_UNRAID))

    umasks = {}
    for c in containers:
        value = c["variables"].get("UMASK") or c["variables"].get("UMASK_SET")
        if value:
            umasks.setdefault(value, []).append(c["name"])
    unusual = {v: n for v, n in umasks.items() if v.lstrip("0") not in ("2", "22")}
    if len(umasks) > 1 or unusual:
        findings.append(finding(
            "ids|UMASK", AREA, TIP, "Unraid",
            "Your media containers set different file permissions (UMASK)",
            "UMASK sets who can change the files a container creates. The TRaSH guide uses 002 everywhere, so every "
            "container in the group can manage every file.",
            steps=["Set UMASK to 002 in every container below, then restart them."],
            items=[f"{', '.join(sorted(n))}: UMASK={v}" for v, n in sorted(umasks.items())], link=TRASH_UNRAID))
    return findings


def check_shares(shares, media_containers):
    findings = []
    by_name = {s["name"]: s for s in shares}
    for name in ("appdata", "system"):
        share = by_name.get(name)
        if share and share.get("shareUseCache") in ("yes", "no"):
            findings.append(finding(
                f"share|{name}", AREA, WARNING, "Unraid",
                f"The {name} share can end up on the array",
                f"{name} is set to {'move to the array' if share['shareUseCache'] == 'yes' else 'live on the array'}. "
                "Container databases are much slower there, and the mover can't move files a running container has "
                "open, so they get stuck half moved.",
                steps=[f"In Shares > {name}, set the primary storage to your cache pool and the secondary storage to none "
                       "(on Unraid before 6.12: Use cache pool: Only or Prefer)."],
                link=TRASH_UNRAID))

    used = set()
    for c in media_containers:
        for m in c["data"]:
            where = location(m["host"])
            if where:
                used.add(where[1])
    if len(used) > 1 and any(c["role"] == "arr" for c in media_containers):
        findings.append(finding(
            "share|split", AREA, TIP, "Unraid",
            "Downloads and media are spread over several shares",
            "Hardlinks only work inside one share. The TRaSH guide keeps torrents, usenet and media in a single share "
            "(usually called data), so moves are instant and seeding files don't take up space twice.",
            steps=["Move everything into one share, e.g. data/torrents, data/usenet and data/media, and map it as /data."],
            items=sorted(used), link=TRASH_UNRAID))
    return findings


def summary(snapshot):
    """What was found in the upload, for the Unraid page."""
    ignored = set(snapshot.get("ignored", []))
    out = []
    for c in snapshot["containers"]:
        kind, app = role(c)
        out.append({"name": c["name"], "image": c["image"], "role": kind, "app": app, "ignored": c["name"] in ignored,
                    "mounts": [f"{m['host']} → {m['container']}" for m in c["mounts"]]})
    return {"containers": out, "shares": [s["name"] for s in snapshot["shares"]], "imported_at": snapshot.get("imported_at")}
