"""Reading Unraid's Docker templates and share settings, and checking them.

Setuparr never looks around the server itself. It reads only the files it's
given: the Docker templates (/boot/config/plugins/dockerMan/templates-user),
the share settings (/boot/config/shares), Compose Manager's compose files,
Unraid's Docker, share and network settings (/boot/config/docker.cfg,
share.cfg and network.cfg) and the installed plugins (/boot/config/plugins/*.plg).
The terminal command also sends the Unraid version and the PCI IDs of the
server's devices (lspci -n), so the GPU and network card checks know what's there.

Those files can hold VPN passwords, API keys and other personal details, so
they're read in memory and only what the checks need is kept: each container's
name, image and folder mappings, its PUID, PGID and UMASK, and whether it's
given a GPU or a custom network; the few Docker, share and network settings the
checks below look at (never IP addresses); each plugin's file name and where it
came from. Nothing else is stored: not passwords, keys, other variables, ports,
network names or time zones.
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
# The only settings kept from Unraid's own settings files
KEPT_SETTINGS = {
    "docker": ("DOCKER_ENABLED", "DOCKER_IMAGE_TYPE", "DOCKER_IMAGE_FILE", "DOCKER_NETWORK_TYPE"),
    "share": ("shareUserExclusive", "fuse_useino"),
    "network": ("BRIDGING",),
}
SETTINGS_FILES = {"docker.cfg": "docker", "share.cfg": "share", "network.cfg": "network"}
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
# Docker networks that aren't custom (macvlan or ipvlan) ones
BUILT_IN_NETWORKS = {"", "bridge", "host", "none"}

UNRAID_EXCLUSIVE = "https://unraid.net/blog/6-12-0-stable"
UNRAID_MACVLAN = "https://docs.unraid.net/unraid-os/release-notes/6.12.4/"
REALTEK_PLUGIN = "https://forums.unraid.net/topic/141349-plugin-realtek-r8125-r8168-and-r81526-drivers/"
NVIDIA_PLUGIN = "https://forums.unraid.net/topic/98978-plugin-nvidia-driver/"
LSIO_GPU = "https://docs.linuxserver.io/images/docker-plex/#hardware-acceleration"
# Community Applications' moderation list: the deprecated and blacklisted plugins and containers that Fix Common
# Problems warns about too
MODERATION_URL = "https://raw.githubusercontent.com/Squidly271/Community-Applications-Moderators/master/Moderation.json"


class UploadError(ValueError):
    pass


# Reading the files


def read_upload(files):
    """[(name, bytes)] -> {"templates", "compose", "shares", "settings", "plugins": [(name, bytes)]}, archives opened."""
    found = {"templates": [], "compose": [], "shares": [], "settings": [], "plugins": []}
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
        folder = posixpath.basename(posixpath.dirname(lower))
        if base.endswith(".plg") and folder in ("", "plugins"):
            found["plugins"].append((base, data))
        elif base in SETTINGS_FILES and "/shares/" not in "/" + lower:
            found["settings"].append((base, data))
        elif base.endswith(".xml"):
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
    mounts, variables, devices = [], [], []
    for c in root.findall("Config"):
        kind, target, value = c.get("Type", ""), (c.get("Target") or "").strip(), (c.text or "").strip()
        if kind == "Path" and value and target:
            mounts.append({"host": value, "container": target, "mode": c.get("Mode", "rw")})
        elif kind == "Variable":
            variables.append((target, value))
        elif kind == "Device":
            devices.append(value)
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
        "custom_network": text("Network").lower() not in BUILT_IN_NETWORKS and not text("Network").startswith("container:"),
        "gpu": gpu_access(devices, text("ExtraParams"), dict(variables)),
    }


def gpu_access(devices, extra, variables, nvidia_runtime=False):
    """Whether a container is given an Intel or AMD GPU (/dev/dri), or an Nvidia one (the Nvidia runtime and
    NVIDIA_VISIBLE_DEVICES). Only these two yes/no answers are kept, not the parameters themselves."""
    extra = extra or ""
    dri = any("/dev/dri" in d for d in devices) or bool(re.search(r"--device[= ]+/dev/dri", extra))
    runtime = nvidia_runtime or bool(re.search(r"--runtime[= ]+nvidia|--gpus[= ]", extra))
    return {"dri": dri, "nvidia": runtime and bool(str(variables.get("NVIDIA_VISIBLE_DEVICES") or "").strip())}


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
        pairs = list(pairs)
        reserved = (((spec.get("deploy") or {}).get("resources") or {}).get("reservations") or {}).get("devices") or []
        reserved_nvidia = any(isinstance(d, dict) and d.get("driver") == "nvidia" for d in reserved)
        variables = dict(pairs)
        if reserved_nvidia:
            # Compose's own way of giving a container an Nvidia GPU needs no variable
            variables.setdefault("NVIDIA_VISIBLE_DEVICES", "all")
        containers.append({
            "name": spec.get("container_name") or service,
            "source": "compose",
            "image": str(spec.get("image", "")),
            "mounts": mounts,
            "variables": kept_variables(pairs),
            "custom_network": any(str(n).startswith(("br", "eth", "bond")) for n in spec.get("networks") or {}),
            "gpu": gpu_access([str(d) for d in spec.get("devices") or []], "", variables,
                              spec.get("runtime") == "nvidia" or reserved_nvidia),
        })
    return containers


def parse_cfg(data):
    """Unraid's KEY="value" settings files. An interface's settings in network.cfg, like BRIDGING[0], are read as
    eth0's, with no index."""
    values = {}
    for line in data.decode("utf-8", "replace").splitlines():
        match = re.match(r'^\s*(\w+)(\[0\])?\s*=\s*"?(.*?)"?\s*$', line)
        if match and match.group(1) not in values:
            values[match.group(1)] = match.group(3)
    return values


def parse_settings(name, data):
    kind = SETTINGS_FILES[name]
    values = parse_cfg(data)
    return kind, {k: values[k] for k in KEPT_SETTINGS[kind] if k in values}


def parse_plugin(name, data):
    """An installed plugin: its file name, and the address it was installed from (which is how Community
    Applications' moderation list names it). Its scripts and everything else are dropped."""
    url = ""
    try:
        root = ET.fromstring(data)
        if root.tag == "PLUGIN":
            url = (root.get("pluginURL") or "").strip()
    except ET.ParseError:
        # Some use an entity Python can't expand; the address is still in the DOCTYPE
        match = re.search(rb'<!ENTITY\s+pluginURL\s+"([^"]+)"', data)
        if match:
            url = match.group(1).decode("utf-8", "replace")
    if "&" in url or not url.startswith(("http://", "https://")):
        url = ""
    return {"file": name, "url": url}


def parse_hardware(header):
    """lspci -n, as "class:vendor:device" words (e.g. 0300:8086:4680), into a list of devices. Anything else is
    dropped."""
    devices = []
    for word in (header or "").split()[:300]:
        match = re.fullmatch(r"([0-9a-fA-F]{4}):([0-9a-fA-F]{4}):([0-9a-fA-F]{4})", word.strip())
        if match:
            devices.append({"class": match.group(1).lower(), "vendor": match.group(2).lower(), "device": match.group(3).lower()})
    return devices


def parse_version(text):
    match = re.search(r"\d+\.\d+(\.\d+)?(-[\w.]+)?", text or "")
    return match.group(0) if match else None


def version_tuple(version):
    return tuple(int(n) for n in re.findall(r"\d+", (version or "").split("-")[0])[:3])


def parse_share(name, data):
    values = parse_cfg(data)
    keep = ("shareUseCache", "shareCachePool", "shareCachePool2", "shareAllocator", "shareSplitLevel")
    return {"name": name[:-4], **{k: values[k] for k in keep if k in values}}


def build_snapshot(raw, hardware=None, version=None):
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
    settings = dict(parse_settings(n, d) for n, d in raw.get("settings", []))
    plugins = sorted((parse_plugin(n, d) for n, d in raw.get("plugins", [])), key=lambda p: p["file"])
    if not containers and not shares and not settings and not plugins:
        raise UploadError("No Docker templates, compose files or share settings were found in what you uploaded")
    snap = {"containers": sorted(containers, key=lambda c: c["name"].lower()),
            "shares": sorted(shares, key=lambda s: s["name"].lower()),
            "settings": settings}
    # Left out when they weren't sent, so the checks that need them know not to guess
    if plugins:
        snap["plugins"] = plugins
    if hardware is not None:
        snap["hardware"] = parse_hardware(hardware)
    if parse_version(version):
        snap["unraid_version"] = parse_version(version)
    return snap


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


def check(snapshot, moderation=None):
    """moderation: Community Applications' moderation list, or None if it couldn't be downloaded."""
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
    settings = snapshot.get("settings") or {}
    findings += check_hardlink_support(settings.get("share"), arrs)
    findings += check_exclusive(settings.get("share"), snapshot["shares"], containers)
    findings += check_docker_storage(settings.get("docker"), settings.get("share"), snapshot["shares"])
    findings += check_macvlan(settings.get("docker"), settings.get("network"), containers, snapshot.get("unraid_version"))
    findings += check_moderation(snapshot.get("plugins"), containers, moderation)
    findings += check_recommended_plugins(snapshot.get("plugins"))
    findings += check_hardware(snapshot.get("hardware"), snapshot.get("plugins"),
                               [c for c in containers if c["role"] == "server"])
    return sent(snapshot, findings)


def sent(snapshot, findings):
    """Mark findings that come from the Unraid settings with when they were sent, since they don't change until
    the settings are sent again."""
    for f in findings:
        f["unraid_sent"] = snapshot.get("imported_at")
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
    for name in sorted(used):
        share = by_name.get(name)
        if share and share.get("shareUseCache") == "prefer":
            findings.append(finding(
                f"share-prefer|{name}", AREA, WARNING, "Unraid",
                f"The mover fills your cache with the {name} share",
                f"The {name} share holds downloads or media, and the mover is set to move it from the array to the "
                "cache. A media library soon outgrows a cache pool, and once the cache is full, downloads fail.",
                steps=[f"In Shares > {name}, set the primary storage to your cache pool, the secondary storage to the "
                       "array and the mover action to Cache -> Array (on Unraid before 6.12: Use cache pool: Yes)."],
                link=TRASH_UNRAID))

    if len(used) > 1 and any(c["role"] == "arr" for c in media_containers):
        findings.append(finding(
            "share|split", AREA, TIP, "Unraid",
            "Downloads and media are spread over several shares",
            "Hardlinks only work inside one share. The TRaSH guide keeps torrents, usenet and media in a single share "
            "(usually called data), so moves are instant and seeding files don't take up space twice.",
            steps=["Move everything into one share, e.g. data/torrents, data/usenet and data/media, and map it as /data."],
            items=sorted(used), link=TRASH_UNRAID))
    return findings


# Unraid's own settings


def share_of(path):
    """("user", share), ("disk", share) or ("array", share) for a host path, or None."""
    parts = clean(path).split("/")
    if len(parts) < 4 or parts[1] != "mnt":
        return None
    if parts[2] in ("user", "user0"):
        return "user", parts[3]
    if re.match(r"disk\d+$", parts[2]):
        return "array", parts[3]
    if parts[2] not in NOT_POOLS:
        return "disk", parts[3]
    return None


def check_hardlink_support(share_settings, arrs):
    if not arrs or (share_settings or {}).get("fuse_useino") != "no":
        return []
    return [finding(
        "tunable-hardlinks", AREA, PROBLEM, "Unraid",
        "Unraid's hard link support is turned off",
        "With Tunable (support Hard Links) set to No, Unraid's user shares can't make hardlinks, so Sonarr and Radarr "
        "copy every download even when your folders are laid out the TRaSH way. The TRaSH guide's first step is turning "
        "it on.",
        steps=["Stop the array, then in Settings > Global Share Settings set Tunable (support Hard Links) to Yes.",
               "Start the array again."],
        link=TRASH_UNRAID)]


def check_exclusive(share_settings, shares, containers):
    """appdata and system bypass Unraid's FUSE layer only as exclusive shares (Unraid 6.12 and later)."""
    if share_settings is None or "shareUserExclusive" not in share_settings:
        return []
    by_name = {s["name"]: s for s in shares}
    through_fuse = {}
    for c in containers:
        for m in c["mounts"]:
            where = share_of(m["host"])
            if where and where[0] == "user" and where[1] in ("appdata", "system"):
                through_fuse.setdefault(where[1], []).append(c["name"])
    findings = []
    for name, users in sorted(through_fuse.items()):
        share = by_name.get(name)
        if not share or share.get("shareUseCache") != "only":
            continue  # check_shares already says when it can end up on the array
        if share_settings["shareUserExclusive"] != "yes":
            findings.append(finding(
                f"exclusive|{name}", AREA, TIP, "Unraid",
                f"The {name} share goes through Unraid's slower FUSE layer",
                f"Containers reach {name} through /mnt/user, which goes through Unraid's FUSE layer (shfs) and slows "
                "down databases. Since Unraid 6.12, a share that lives only on a pool can be an exclusive share instead: "
                "/mnt/user then points straight at the pool, with no change to your container paths.",
                steps=["Stop the array, then in Settings > Global Share Settings set Permit exclusive shares to Yes.",
                       f"Start the array. Shares > {name} should now say Exclusive access: Yes. If it doesn't, the share "
                       "also has files on another disk or pool, or is exported over NFS."],
                items=sorted(set(users)), link=UNRAID_EXCLUSIVE))
    return findings


def check_docker_storage(docker, share_settings, shares):
    """Where Docker keeps its image or folder, following Unraid's own help text on Settings > Docker."""
    if not docker or docker.get("DOCKER_ENABLED") == "no" or not docker.get("DOCKER_IMAGE_FILE"):
        return []
    path = docker["DOCKER_IMAGE_FILE"]
    folder = docker.get("DOCKER_IMAGE_TYPE") == "folder"
    what = "Docker's folder" if folder else "Docker's image file (docker.img)"
    where = share_of(path)
    if not where:
        return []
    kind, name = where
    share = {s["name"]: s for s in shares}.get(name, {})
    if kind == "array" or (kind == "user" and share.get("shareUseCache") in ("yes", "no") and name != "system"):
        return [finding(
            "docker-storage", AREA, WARNING, "Unraid",
            f"{what} can end up on the array",
            f"{what} is at {path}, which can sit on the array's hard drives. Every container then runs from them: "
            "slower, and the drives never spin down. Unraid recommends keeping it on a pool, ideally an SSD.",
            steps=["Stop Docker in Settings > Docker (Enable Docker: No).",
                   f"Move it to a pool-only share, e.g. {'/mnt/user/system/docker/' if folder else '/mnt/user/system/docker/docker.img'}, "
                   "set the new location there, then start Docker again."],
            items=[path])]
    exclusive = (share_settings or {}).get("shareUserExclusive")
    if folder and kind == "user" and share.get("shareUseCache") == "only" and exclusive == "no":
        return [finding(
            "docker-folder-fuse", AREA, WARNING, "Unraid",
            "Docker's folder goes through Unraid's FUSE layer",
            f"Docker keeps its files as a folder at {path}, and through /mnt/user that goes through Unraid's FUSE layer "
            "(shfs), which is slow and can cause problems with Docker's own storage driver.",
            steps=["Stop the array, then in Settings > Global Share Settings set Permit exclusive shares to Yes, so "
                   f"/mnt/user/{name} points straight at the pool.",
                   f"Or stop Docker and point it at the pool path instead, e.g. /mnt/{share.get('shareCachePool') or 'cache'}"
                   f"{path[len('/mnt/user'):]}."],
            items=[path], link=UNRAID_EXCLUSIVE)]
    return []


def check_macvlan(docker, network, containers, version):
    """macvlan custom networks with bridging on caused crashes (call traces) on Unraid 6.12.10 and earlier."""
    if not docker or not network or not version or version_tuple(version) > (6, 12, 10):
        return []
    on_custom = sorted(c["name"] for c in containers if c.get("custom_network"))
    macvlan = docker.get("DOCKER_NETWORK_TYPE") != "1"
    if not on_custom or not macvlan or network.get("BRIDGING") != "yes":
        return []
    steps = ["Stop Docker in Settings > Docker, then set Docker custom network type to ipvlan.",
             "If your router needs each container to have its own MAC address (ipvlan shares one), instead set Enable "
             "Bridging to No in Settings > Network Settings > eth0, and Host access to custom networks to Enabled in "
             "Settings > Docker."]
    if version_tuple(version) < (6, 12, 4):
        steps = steps[:1] + ["Or update Unraid to 6.12.4 or later, which can run macvlan with bridging off."]
    return [finding(
        "macvlan", AREA, WARNING, "Unraid",
        "Containers with their own IP address can crash this Unraid version",
        f"Unraid {version} uses macvlan for custom Docker networks with bridging on. Unraid's release notes say this "
        "combination causes \"macvlan call traces\", which can freeze or crash the server.",
        steps=steps, items=on_custom, link=UNRAID_MACVLAN)]


# Plugins and drivers


def plain(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip()


def image_names(image):
    """The names Community Applications might list an image under."""
    image = image.lower().split("@")[0]
    for prefix in ("docker.io/", "index.docker.io/", "registry.hub.docker.com/"):
        if image.startswith(prefix):
            image = image[len(prefix):]
    image = image.removeprefix("library/")
    names = {image}
    if ":" in image.rsplit("/", 1)[-1]:
        names.add(image.rsplit(":", 1)[0])
    else:
        names.add(image + ":latest")
    return names


def check_moderation(plugins, containers, moderation):
    if not moderation:
        return []
    listed = {k.lower(): v for k, v in moderation.items() if isinstance(v, dict) and (v.get("Blacklist") or v.get("Deprecated"))}
    found = []
    for p in plugins or []:
        if p["url"] and p["url"].lower() in listed:
            found.append(("plugin", p["file"], p["file"], listed[p["url"].lower()]))
    for c in containers:
        entry = next((listed[n] for n in sorted(image_names(c["image"])) if n in listed), None)
        if entry:
            found.append(("container", c["name"], c["image"], entry))
    findings = []
    for kind, name, what, entry in found:
        blacklisted = bool(entry.get("Blacklist"))
        reason = plain(entry.get("ModeratorComment")) or ("It's no longer maintained." if not blacklisted else "")
        findings.append(finding(
            f"moderation|{kind}|{name}", AREA, PROBLEM if blacklisted else WARNING, "Unraid",
            f"The {name} {kind} is {'blacklisted' if blacklisted else 'deprecated'} in Community Applications",
            f"Community Applications' moderators have {'blacklisted' if blacklisted else 'deprecated'} {what}. "
            f"{reason}".strip(),
            steps=[f"Remove it from the {'Plugins' if kind == 'plugin' else 'Docker'} page"
                   + (", and install the replacement the moderators suggest from Apps." if not blacklisted else
                      ". Leaving it installed can cause problems on your server.")],
            items=[what]))
    return findings


def installed(plugins, *files):
    return any(p["file"] in files for p in plugins or [])


# Plugins most Unraid servers should have, and why
RECOMMENDED_PLUGINS = [
    (("fix.common.problems.plg",), "Fix Common Problems",
     "It checks the server for dozens of known problems Setuparr can't see, such as full drives, disk errors and "
     "out-of-date or broken plugins."),
    (("appdata.backup.plg", "appdata.backup.beta.plg"), "Appdata Backup",
     "It backs up every container's settings in appdata, which is the only copy of your Sonarr, Radarr and media "
     "server databases."),
]


def check_recommended_plugins(plugins):
    if not plugins:
        return []
    findings = []
    for files, name, why in RECOMMENDED_PLUGINS:
        if not installed(plugins, *files):
            findings.append(finding(
                f"plugin-missing|{files[0]}", AREA, TIP, "Unraid", f"The {name} plugin isn't installed", why,
                steps=[f"In Unraid, open Apps, search for {name} and install it."]))
    return findings


GPU_CLASSES = ("0300", "0302", "0380")
GPU_VENDORS = {"8086": "Intel", "1002": "AMD", "10de": "Nvidia"}
REALTEK_DRIVERS = [
    # (PCI IDs, plugin files, driver) from Fix Common Problems' Realtek check
    ({"10ec:8125", "10ec:5000", "10ec:3000", "10ec:8162"}, ("unraid-r8125.plg",), "RTL8125"),
    ({"10ec:8126"}, ("unraid-r8126.plg", "unraid-r8125.plg"), "RTL8126"),
    ({"10ec:8168", "10ec:8161", "10ec:2502", "10ec:2600", "1186:4300", "1186:4b10"}, ("unraid-r8168.plg",), "RTL8168"),
]
CORAL = {"1ac1:089a"}


def check_hardware(hardware, plugins, servers):
    """Drivers for the GPUs, network cards and Coral accelerators found, and whether the media servers get the GPU."""
    if not hardware:
        return []
    ids = {f"{d['vendor']}:{d['device']}" for d in hardware}
    gpus = sorted({GPU_VENDORS[d["vendor"]] for d in hardware if d["class"] in GPU_CLASSES and d["vendor"] in GPU_VENDORS})
    findings = []
    # The driver plugins only tell us anything if the plugins were sent
    if plugins is not None:
        if "Nvidia" in gpus and not installed(plugins, "nvidia-driver.plg"):
            findings.append(finding(
                "driver|nvidia", AREA, WARNING, "Unraid", "Your Nvidia GPU has no driver",
                "Unraid doesn't come with Nvidia's driver, so no container can use the GPU for transcoding until the "
                "Nvidia Driver plugin is installed.",
                steps=["In Unraid, open Apps, search for Nvidia Driver and install it, then reboot or restart Docker."],
                link=NVIDIA_PLUGIN))
        if "Intel" in gpus and not installed(plugins, "intel-gpu-top.plg"):
            findings.append(finding(
                "driver|intel", AREA, TIP, "Unraid", "Intel GPU TOP isn't installed",
                "The Intel GPU TOP plugin makes sure the Intel GPU driver is loaded, and shows how busy the GPU is, "
                "so you can see that Plex or Jellyfin is really transcoding on it.",
                steps=["In Unraid, open Apps, search for Intel GPU TOP and install it."]))
        if "AMD" in gpus and not installed(plugins, "radeontop.plg"):
            findings.append(finding(
                "driver|amd", AREA, TIP, "Unraid", "Radeon TOP isn't installed",
                "The Radeon TOP plugin loads the AMD GPU driver and shows how busy the GPU is.",
                steps=["In Unraid, open Apps, search for Radeon TOP and install it."]))
        for pci, files, driver in REALTEK_DRIVERS:
            if ids & pci and not installed(plugins, *files):
                findings.append(finding(
                    f"driver|{driver}", AREA, TIP, "Unraid", f"Your Realtek {driver} network card uses Unraid's built-in driver",
                    "Unraid's built-in driver for this card is known to drop out or run slowly. If you see network "
                    "problems, Realtek's own driver from the plugin usually fixes them.",
                    steps=[f"In Unraid, open Apps, search for {driver[:6]} and install the driver plugin, then reboot."],
                    link=REALTEK_PLUGIN))
        if ids & CORAL and not installed(plugins, "coral-driver.plg"):
            findings.append(finding(
                "driver|coral", AREA, WARNING, "Unraid", "Your Coral accelerator has no driver",
                "Unraid doesn't come with the driver for a PCIe or M.2 Coral, so Frigate and other containers can't "
                "use it.",
                steps=["In Unraid, open Apps, search for Coral Accelerator Module Drivers and install it, then reboot."]))

    for server in servers:
        gpu = server.get("gpu") or {}
        if not gpus or gpu.get("dri") or gpu.get("nvidia"):
            continue
        name = f"{server['app']} ({server['name']})" if server["app"].lower() != server["name"].lower() else server["app"]
        steps = []
        if "Intel" in gpus or "AMD" in gpus:
            steps.append(f"For the {' or '.join(g for g in gpus if g != 'Nvidia')} GPU: edit {server['name']}, add a "
                         "Device (Add another Path, Port, Variable, Label or Device), and set it to /dev/dri.")
        if "Nvidia" in gpus:
            steps.append(f"For the Nvidia GPU: edit {server['name']} in Advanced View, add --runtime=nvidia to Extra "
                         "Parameters, and add the variables NVIDIA_VISIBLE_DEVICES (your GPU's ID from the Nvidia Driver "
                         "plugin page, or all) and NVIDIA_DRIVER_CAPABILITIES=all.")
        steps.append(f"Then turn on hardware transcoding in {server['app']}'s own settings"
                     + (" (Plex needs Plex Pass for it)." if server["app"] == "Plex" else "."))
        findings.append(finding(
            f"gpu|{server['name']}", AREA, WARNING, name, f"{name} can't use your GPU for transcoding",
            f"This server has {' and '.join(gpus)} graphics, but {server['name']} isn't given the GPU, so every "
            "transcode runs on the CPU: slower, hotter, and fewer streams at once.",
            steps=steps, items=[f"{g} GPU" for g in gpus], link=LSIO_GPU))
    return findings


def summary(snapshot):
    """What was found in the upload, for the Unraid page."""
    ignored = set(snapshot.get("ignored", []))
    out = []
    for c in snapshot["containers"]:
        kind, app = role(c)
        out.append({"name": c["name"], "image": c["image"], "role": kind, "app": app, "ignored": c["name"] in ignored,
                    "mounts": [f"{m['host']} → {m['container']}" for m in c["mounts"]]})
    return {"containers": out, "shares": [s["name"] for s in snapshot["shares"]], "imported_at": snapshot.get("imported_at"),
            "settings": sorted(f"{k}.cfg" for k in snapshot.get("settings") or {}),
            "plugins": [p["file"] for p in snapshot.get("plugins") or []],
            "hardware": len(snapshot["hardware"]) if "hardware" in snapshot else None,
            "unraid_version": snapshot.get("unraid_version")}
