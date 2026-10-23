"""Reading Unraid's Docker templates and share settings, and checking them.

Setuparr never looks around the server itself. It reads only the files it's
given: the Docker templates (/boot/config/plugins/dockerMan/templates-user),
the share settings (/boot/config/shares), Compose Manager's compose files,
Unraid's Docker, share, network, identification and display settings
(/boot/config/docker.cfg, share.cfg, network.cfg, ident.cfg and
plugins/dynamix/dynamix.cfg), the installed plugins (/boot/config/plugins/*.plg)
and Appdata Backup's settings. The terminal command also sends the Unraid
version, the PCI IDs of the server's devices (lspci -n), the state of the disks
(/var/local/emhttp/disks.ini) and the order containers start in
(/var/lib/docker/unraid-autostart), the names of the containers Docker has
(installed ones), the names of the folders in appdata, and how much space
Docker's unused images and volumes take.

Those files can hold VPN passwords, API keys and other personal details, so
they're read in memory and only what the checks need is kept: each container's
name, image, folder mappings and host ports, its PUID, PGID and UMASK, whether
it's given a GPU or a custom network, which container's network it uses, and
whether its time zone matches Unraid's; the few Unraid settings the checks below
look at (never IP addresses, server names, email settings or time zones); each
disk's slot name, state, size, free space, errors and temperature (never serial
numbers or device names); each plugin's file name, where it came from and the
Unraid versions it's made for; and Appdata Backup's schedule, destination and
skipped containers; the names of appdata folders and installed containers; and
the size of Docker's unused images and volumes. Nothing else is stored: not passwords, keys, other
variables, network names or time zones.
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
    "docker": ("DOCKER_ENABLED", "DOCKER_IMAGE_TYPE", "DOCKER_IMAGE_FILE", "DOCKER_NETWORK_TYPE", "DOCKER_LOG_ROTATION"),
    "share": ("shareUserExclusive", "fuse_useino", "shareMoverSchedule"),
    "network": ("BRIDGING",),
    "ident": ("USE_NTP", "USE_SSL", "PORT", "PORTSSL"),
    # dynamix.cfg has sections; its email settings are never kept
    "dynamix": ("parity.mode", "notify.alert", "notify.warning"),
}
SETTINGS_FILES = {"docker.cfg": "docker", "share.cfg": "share", "network.cfg": "network", "ident.cfg": "ident",
                  "dynamix.cfg": "dynamix"}
# Unraid's own defaults for the dynamix.cfg settings above, used when they aren't in the file
DYNAMIX_DEFAULTS = {"parity.mode": "0", "notify.alert": "1", "notify.warning": "1"}
# What's kept of each disk in disks.ini: no serial numbers (id) or device names
KEPT_DISK = ("name", "type", "status", "fsSize", "fsFree", "numErrors")
BACKUP_FOLDERS = ("appdata.backup", "appdata.backup.beta")
MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 50 * 1024 * 1024
MAX_FILES = 5000
MAX_APPDATA = 1000
# The cleanup finding for Docker's unused images and volumes only shows from this size
DOCKER_UNUSED_BYTES = 1024 ** 3
APPDATA = re.compile(r"^/mnt/[^/]+/appdata(?:/([^/]+))?")
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
# Setuparr's own Extra Parameters guide
GUIDE = "#/guides"
UNRAID_DOCS = "https://docs.unraid.net/unraid-os/manual/"
UNRAID_PARITY = "https://docs.unraid.net/unraid-os/manual/storage-management/#parity-checks"
UNRAID_NOTIFY = "https://docs.unraid.net/unraid-os/manual/additional-settings/#notification-settings"
UNRAID_MOVER = "https://docs.unraid.net/unraid-os/manual/shares/user-shares/#minimum-free-space"
APPDATA_BACKUP = "https://forums.unraid.net/topic/137710-plugin-appdatabackup/"
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
    """[(name, bytes)] -> {"templates", "compose", "shares", "settings", "plugins", "disks", "autostart", "backup":
    [(name, bytes)]}, archives opened."""
    found = {"templates": [], "compose": [], "shares": [], "settings": [], "plugins": [], "disks": [], "autostart": [],
             "backup": []}
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
        elif base == "disks.ini":
            found["disks"].append((base, data))
        elif base == "unraid-autostart":
            found["autostart"].append((base, data))
        elif base == "config.json" and folder in BACKUP_FOLDERS:
            found["backup"].append((folder, data))
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
    mounts, variables, devices, ports = [], [], [], []
    for c in root.findall("Config"):
        kind, target, value = c.get("Type", ""), (c.get("Target") or "").strip(), (c.text or "").strip()
        if kind == "Path" and value and target:
            mounts.append({"host": value, "container": target, "mode": c.get("Mode", "rw")})
        elif kind == "Variable":
            variables.append((target, value))
        elif kind == "Device":
            devices.append(value)
        elif kind == "Port":
            ports.append((value, target, c.get("Mode") or "tcp"))
    # Version 1 templates
    for v in root.findall("Data/Volume"):
        host, target = (v.findtext("HostDir") or "").strip(), (v.findtext("ContainerDir") or "").strip()
        if host and target:
            mounts.append({"host": host, "container": target, "mode": (v.findtext("Mode") or "rw").strip()})
    for v in root.findall("Environment/Variable"):
        variables.append(((v.findtext("Name") or "").strip(), (v.findtext("Value") or "").strip()))
    for p in root.findall("Networking/Publish/Port"):
        ports.append(((p.findtext("HostPort") or "").strip(), (p.findtext("ContainerPort") or "").strip(),
                      (p.findtext("Protocol") or "tcp").strip()))
    container = text("Name") or re.sub(r"^my-|\.xml$", "", name)
    network = text("Network") or text("Networking/Mode")
    joined = re.search(r"--(?:net|network)(?:=|\s+)['\"]?container:([^'\"\s]+)", text("ExtraParams"))
    if joined:
        network = "container:" + joined.group(1)
    kind = network_kind(network)
    return {
        "name": container,
        "source": "template",
        "image": text("Repository"),
        "mounts": mounts,
        "variables": kept_variables(variables),
        "custom_network": network.lower() not in BUILT_IN_NETWORKS and not network.startswith("container:"),
        "network": kind,
        "network_of": network.split(":", 1)[1].strip() if kind == "container" else None,
        "ports": published_ports(ports, kind),
        "gpu": gpu_access(devices, text("ExtraParams"), dict(variables)),
        "flags": extra_flags(text("ExtraParams")),
        "privileged": text("Privileged").lower() == "true" or "--privileged" in extra_flags(text("ExtraParams")),
        # Unraid gives every container its own time zone unless the template sets TZ. Only whether the two match is
        # kept (in build_snapshot), never the time zone.
        "_tz": dict(variables).get("TZ") or None,
    }


def extra_flags(extra):
    """The names of the flags in Extra Parameters (--restart, --memory...), never their values, which can hold
    passwords."""
    return sorted(set(re.findall(r"(?:^|\s)(--[a-z][a-z-]*)", extra or "")))[:50]


def network_kind(network):
    """bridge (ports are published on Unraid's address), host (the app uses Unraid's address and its own port),
    own_ip (macvlan or ipvlan, e.g. br0: ports aren't published), container (another container's network) or none.
    Any other name is a network made with docker network create, which is a bridge."""
    network = (network or "").strip().lower()
    if network.startswith(("container:", "service:")):
        return "container"
    if network in ("host", "none"):
        return network
    if re.fullmatch(r"(br|eth|bond|wlan|vhost)\d+(\.\d+)?", network):
        return "own_ip"
    return "bridge"


def published_ports(ports, kind):
    """[(host port, container port, protocol)] -> ["8989/tcp"], the ports a container takes on Unraid's address."""
    if kind not in ("bridge", "host"):
        return []
    out = set()
    for host, target, protocol in ports:
        port = (target if kind == "host" else host) or ""
        protocol = "udp" if str(protocol).lower() == "udp" else "tcp"
        if re.fullmatch(r"\d{1,5}", port.strip()) and 0 < int(port) < 65536:
            out.add(f"{int(port)}/{protocol}")
    return sorted(out, key=lambda p: (int(p.split("/")[0]), p))


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
        mode = str(spec.get("network_mode") or "")
        kind = network_kind(mode)
        tz = variables.get("TZ")
        if reserved_nvidia:
            # Compose's own way of giving a container an Nvidia GPU needs no variable
            variables.setdefault("NVIDIA_VISIBLE_DEVICES", "all")
        own_ip = any(network_kind(str(n)) == "own_ip" for n in spec.get("networks") or {})
        containers.append({
            "name": spec.get("container_name") or service,
            "source": "compose",
            "image": str(spec.get("image", "")),
            "mounts": mounts,
            "variables": kept_variables(pairs),
            "custom_network": own_ip,
            "network": "own_ip" if own_ip and kind == "bridge" else kind,
            "network_of": mode.split(":", 1)[1].strip() if kind == "container" else None,
            "ports": [] if own_ip else published_ports(compose_ports(spec.get("ports")), kind),
            "gpu": gpu_access([str(d) for d in spec.get("devices") or []], "", variables,
                              spec.get("runtime") == "nvidia" or reserved_nvidia),
            "privileged": spec.get("privileged") is True,
            # Compose doesn't add Unraid's time zone, so a container without TZ runs on UTC
            "_tz": str(tz) if tz else "UTC",
        })
    return containers


def compose_ports(ports):
    """Compose's ports, short ("8080:80/udp", "127.0.0.1:8080:80") or long syntax, as (host, container, protocol).
    Ports Docker picks itself, and ranges, are left out."""
    out = []
    for p in ports or []:
        if isinstance(p, dict):
            out.append((str(p.get("published") or ""), str(p.get("target") or ""), str(p.get("protocol") or "tcp")))
            continue
        spec, _, protocol = str(p).partition("/")
        parts = spec.rsplit(":", 2)
        if len(parts) >= 2:
            out.append((parts[-2], parts[-1], protocol or "tcp"))
    return out


def parse_cfg(data):
    """Unraid's KEY="value" settings files. An interface's settings in network.cfg, like BRIDGING[0], are read as
    eth0's, with no index."""
    values = {}
    for line in data.decode("utf-8", "replace").splitlines():
        match = re.match(r'^\s*(\w+)(\[0\])?\s*=\s*"?(.*?)"?\s*$', line)
        if match and match.group(1) not in values:
            values[match.group(1)] = match.group(3)
    return values


def parse_ini(data):
    """Files with [sections] (dynamix.cfg, disks.ini) -> {section: {key: value}}."""
    sections, current = {}, None
    for line in data.decode("utf-8", "replace").splitlines():
        header = re.match(r'^\s*\["?([^"\]]+)"?\]\s*$', line)
        if header:
            current = sections.setdefault(header.group(1), {})
            continue
        match = re.match(r'^\s*(\w+)\s*=\s*"?(.*?)"?\s*$', line)
        if match and current is not None:
            current.setdefault(match.group(1), match.group(2))
    return sections


def parse_settings(name, data):
    kind = SETTINGS_FILES[name]
    if kind == "dynamix":
        values = {f"{section}.{k}": v for section, keys in parse_ini(data).items() for k, v in keys.items()}
        values = {**DYNAMIX_DEFAULTS, **values}
    else:
        values = parse_cfg(data)
    return kind, {k: values[k] for k in KEPT_SETTINGS[kind] if k in values}


def parse_disks(data):
    """disks.ini: the array and pool slots, their state, size, free space, errors and temperature. Serial numbers
    and device names are dropped."""
    disks = []
    for section, values in parse_ini(data).items():
        if values.get("type") not in ("Parity", "Data", "Cache", "Flash"):
            continue
        disk = {k: values[k] for k in KEPT_DISK if k in values}
        disk["name"] = disk.get("name") or section
        disk["unmountable"] = values.get("fsStatus", "").lower().startswith("unmountable")
        disks.append(disk)
    return disks


def parse_autostart(data):
    """unraid-autostart: the containers that start with Docker, one per line in the order they start, each with an
    optional wait in seconds."""
    return [line.split()[0] for line in data.decode("utf-8", "replace").splitlines() if line.strip()][:500]


def parse_backup(data):
    """Appdata Backup's config.json: its schedule, destination, whether it backs up the flash drive, and which
    containers it skips. Its scripts and everything else are dropped."""
    import json
    try:
        config = json.loads(data)
    except ValueError:
        return None
    if not isinstance(config, dict):
        return None
    skipped = sorted(name for name, c in (config.get("containerSettings") or {}).items()
                     if isinstance(c, dict) and "yes" in (c.get("skip"), c.get("skipBackup")))
    return {"frequency": str(config.get("backupFrequency") or "disabled"),
            "destination": str(config.get("destination") or ""),
            "flash": str(config.get("flashBackup") or "yes"),
            "skipped": skipped}


def parse_plugin(name, data):
    """An installed plugin: its file name, and the address it was installed from (which is how Community
    Applications' moderation list names it). Its scripts and everything else are dropped."""
    url, versions = "", {}
    try:
        root = ET.fromstring(data)
        if root.tag == "PLUGIN":
            url = (root.get("pluginURL") or "").strip()
            versions = {k: root.get(k) or "" for k in ("min", "max")}
    except ET.ParseError:
        # Some use an entity Python can't expand; the address is still in the DOCTYPE
        match = re.search(rb'<!ENTITY\s+pluginURL\s+"([^"]+)"', data)
        if match:
            url = match.group(1).decode("utf-8", "replace")
        for k in ("min", "max"):
            match = re.search(rb'<PLUGIN[^>]*\s' + k.encode() + rb'="([\d.]+)"', data)
            if match:
                versions[k] = match.group(1).decode()
    if "&" in url or not url.startswith(("http://", "https://")):
        url = ""
    plugin = {"file": name, "url": url}
    # The Unraid versions the plugin is made for, when it says
    for k, v in versions.items():
        if re.fullmatch(r"\d+(\.\d+){0,3}", v.strip()):
            plugin[k] = v.strip()
    return plugin


def parse_hardware(header):
    """lspci -n, as "class:vendor:device" words (e.g. 0300:8086:4680), into a list of devices. Anything else is
    dropped."""
    devices = []
    for word in (header or "").split()[:300]:
        match = re.fullmatch(r"([0-9a-fA-F]{4}):([0-9a-fA-F]{4}):([0-9a-fA-F]{4})", word.strip())
        if match:
            devices.append({"class": match.group(1).lower(), "vendor": match.group(2).lower(), "device": match.group(3).lower()})
    return devices


def same_zone(tz):
    """UTC goes by several names."""
    tz = (tz or "").strip()
    return "UTC" if tz.upper() in ("UTC", "ETC/UTC", "GMT", "ETC/GMT", "UNIVERSAL", "ZULU", "ETC/UCT", "UCT") else tz


def parse_version(text):
    match = re.search(r"\d+\.\d+(\.\d+)?(-[\w.]+)?", text or "")
    return match.group(0) if match else None


def version_tuple(version):
    return tuple(int(n) for n in re.findall(r"\d+", (version or "").split("-")[0])[:3])


def parse_share(name, data):
    values = parse_cfg(data)
    keep = ("shareUseCache", "shareCachePool", "shareCachePool2", "shareAllocator", "shareSplitLevel", "shareFloor",
            "shareExport", "shareSecurity")
    return {"name": name[:-4], **{k: values[k] for k in keep if k in values}}


def parse_size(text):
    """Docker's sizes, such as 1.2GB or 300kB, in bytes."""
    m = re.match(r"\s*([\d.]+)\s*([kKMGT]?)B", text or "")
    if not m:
        return 0
    return int(float(m.group(1)) * 1000 ** " KMGT".index(m.group(2).upper() or " "))


def parse_docker_df(header):
    """docker system df's Type|Reclaimable lines, joined with ;. Only the unused images and volumes are kept."""
    out = {}
    for part in (header or "").split(";"):
        kind, _, size = part.partition("|")
        key = {"Images": "images", "Local Volumes": "volumes"}.get(kind.strip())
        if key:
            out[key] = parse_size(size)
    return out or None


def parse_host(host):
    """What the terminal command adds about the server itself: the containers Docker has (space-separated), the
    folders in appdata (separated by /, which can't be in a name) and docker system df."""
    host = host or {}
    installed = (host.get("containers") or "").split()
    appdata = sorted({n.strip() for n in (host.get("appdata") or "").split("/") if n.strip()})[:MAX_APPDATA]
    return {"installed": installed or None, "appdata": appdata if host.get("appdata") is not None else None,
            "docker_unused": parse_docker_df(host.get("docker"))}


def build_snapshot(raw, hardware=None, version=None, host=None):
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
    disks = [d for _, data in raw.get("disks", [])[:1] for d in parse_disks(data)]
    autostart = [parse_autostart(data) for _, data in raw.get("autostart", [])[:1]]
    # The beta plugin's settings, if both are there, are the ones in use only when the beta is installed
    backups = {folder: parse_backup(data) for folder, data in raw.get("backup", [])}
    installed_files = {p["file"] for p in plugins}
    backup = next((backups[f] for f in BACKUP_FOLDERS[::-1] if backups.get(f) and f + ".plg" in installed_files),
                  None) or next((b for b in backups.values() if b), None)
    if not containers and not shares and not settings and not plugins and not disks:
        raise UploadError("No Docker templates, compose files or share settings were found in what you uploaded")
    server_tz = next((parse_cfg(d).get("timeZone") for n, d in raw.get("settings", []) if n == "ident.cfg"), None)
    for c in containers:
        tz = c.pop("_tz", None)
        # Only whether it matches is kept, not the time zone
        if server_tz and (tz or c["source"] == "compose"):
            c["tz_differs"] = same_zone(tz) != same_zone(server_tz)
    host = parse_host(host)
    unused_templates = []
    if host["installed"]:
        # Unraid keeps the template of every container you've ever installed
        installed = set(host["installed"])
        unused_templates = sorted((c["name"] for c in containers if c["source"] == "template" and c["name"] not in installed),
                                  key=str.lower)
        containers = [c for c in containers if c["source"] != "template" or c["name"] in installed]
    snap = {"containers": sorted(containers, key=lambda c: c["name"].lower()),
            "shares": sorted(shares, key=lambda s: s["name"].lower()),
            "settings": settings}
    # Left out when they weren't sent, so the checks that need them know not to guess
    if plugins:
        snap["plugins"] = plugins
    if disks:
        snap["disks"] = disks
    if autostart:
        snap["autostart"] = autostart[0]
    if backup:
        snap["backup"] = backup
    if hardware is not None:
        snap["hardware"] = parse_hardware(hardware)
    if parse_version(version):
        snap["unraid_version"] = parse_version(version)
    if host["installed"]:
        snap["installed"] = sorted(set(host["installed"]), key=str.lower)
        snap["unused_templates"] = unused_templates
    if host["appdata"] is not None:
        snap["appdata"] = host["appdata"]
    if host["docker_unused"]:
        snap["docker_unused"] = host["docker_unused"]
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
                               [c for c in containers if c["role"] == "server"], containers)
    findings += check_plugin_versions(snapshot.get("plugins"), snapshot.get("unraid_version"))
    findings += check_ports(containers, settings.get("ident"))
    findings += check_start_order(containers, snapshot.get("autostart"))
    findings += check_docker_logs(settings.get("docker"))
    findings += check_extra_parameters(containers)
    findings += check_disks(snapshot.get("disks"))
    findings += check_parity_schedule(snapshot.get("disks"), settings.get("dynamix"))
    findings += check_alerts(settings.get("dynamix"))
    findings += check_time(settings.get("ident"), containers)
    findings += check_smb(snapshot["shares"])
    findings += check_mover(settings.get("share"), snapshot["shares"], arrs + clients, snapshot.get("disks"))
    findings += check_backup(snapshot.get("backup"), snapshot.get("plugins"), snapshot["shares"], containers)
    findings += check_cleanup(snapshot)
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


# Ports, start order and Docker's logs


def check_ports(containers, ident):
    """Two containers on the same host port, or one on the port Unraid's web UI uses: Docker won't start the second."""
    ident = ident or {}
    ui = {f"{ident.get('PORT') or '80'}/tcp": "Unraid's web UI"}
    if ident.get("USE_SSL") in ("yes", "auto"):
        ui[f"{ident.get('PORTSSL') or '443'}/tcp"] = "Unraid's web UI (HTTPS)"
    by_port = {}
    for c in containers:
        for port in c.get("ports") or []:
            by_port.setdefault(port, []).append(c["name"])
    findings = []
    for port, names in sorted(by_port.items(), key=lambda p: int(p[0].split("/")[0])):
        number = port.split("/")[0]
        if port in ui:
            findings.append(finding(
                f"port-ui|{port}", AREA, PROBLEM, "Unraid",
                f"{' and '.join(sorted(names))} {'uses' if len(names) == 1 else 'use'} port {number}, which {ui[port]} needs",
                f"Unraid's web UI is on port {number}, so Docker can't give it to a container too: the container won't "
                "start, or if it starts first, you can't open Unraid's web UI.",
                steps=[f"Edit {names[0]} and change the host port {number} to a free one, e.g. 8{number.zfill(3)}. The "
                       "port inside the container (Container Port) stays the same."],
                items=sorted(names)))
        elif len(set(names)) > 1:
            findings.append(finding(
                f"port|{port}", AREA, WARNING, "Unraid",
                f"{plural(len(set(names)), 'container')} want port {number}",
                f"{', '.join(sorted(set(names)))} are all mapped to port {number} on Unraid. Only one container can "
                "have a port, so whichever starts second won't start. If one of them is an old template you don't "
                "use, untick it on the Unraid page.",
                steps=["Edit all but one of them and change the host port to a free one. The port inside the container "
                       "(Container Port) stays the same, and anything that reaches the app at that port needs the new one."],
                items=sorted(set(names))))
    return findings


def check_start_order(containers, autostart):
    """Containers that use another container's network (e.g. qBittorrent through Gluetun) need it running first,
    and the media apps should start by themselves after a reboot."""
    if not autostart:
        return []
    order = {name: i for i, name in enumerate(autostart)}
    findings = []
    for c in containers:
        other = c.get("network_of")
        if not other or c["source"] != "template" or c["name"] not in order:
            continue
        if other not in order:
            findings.append(finding(
                f"start-network|{c['name']}", AREA, PROBLEM, "Unraid",
                f"{c['name']} starts by itself, but {other}, whose network it uses, doesn't",
                f"{c['name']} has no network of its own: it goes through {other}'s. After a reboot {other} isn't "
                f"started, so {c['name']} fails to start too.",
                steps=[f"On the Docker page, turn on Autostart for {other}, and drag it above {c['name']}."],
                items=[c["name"], other]))
        elif order[other] > order[c["name"]]:
            findings.append(finding(
                f"start-order|{c['name']}", AREA, PROBLEM, "Unraid",
                f"{c['name']} starts before {other}, whose network it uses",
                f"{c['name']} goes through {other}'s network, but Unraid starts it first, so after a reboot it fails "
                f"to start, or starts with no network.",
                steps=[f"On the Docker page, unlock the order (the padlock at the top right) and drag {other} above "
                       f"{c['name']}.",
                       f"Then give {other} a wait of 10 seconds or so in its Autostart setting, so it's connected before "
                       f"{c['name']} starts."],
                items=[f"{order[c['name']] + 1}. {c['name']}", f"{order[other] + 1}. {other}"]))

    left_out = sorted(c["name"] for c in containers
                      if c.get("role") and c["source"] == "template" and c["name"] not in order)
    if left_out:
        findings.append(finding(
            "autostart", AREA, TIP, "Unraid",
            f"{plural(len(left_out), 'media container')} "
            + ("doesn't start by itself" if len(left_out) == 1 else "don't start by themselves"),
            "Autostart is off for the containers below, so after a reboot or an Unraid update they stay stopped until "
            "you start them. If one is an old template you don't use, untick it on the Unraid page instead.",
            steps=["On the Docker page, turn on Autostart for each container below."],
            items=left_out))
    return findings


def check_docker_logs(docker):
    if not docker or docker.get("DOCKER_ENABLED") == "no" or docker.get("DOCKER_LOG_ROTATION") != "no":
        return []
    return [finding(
        "docker-logs", AREA, WARNING, "Unraid", "Docker's log rotation is turned off",
        "Each container's log then grows for ever. A chatty container can fill docker.img or your cache pool, and "
        "when that's full, containers stop working.",
        steps=["Stop Docker in Settings > Docker (Enable Docker: No).",
               "Set Docker LOG rotation to Enabled (Unraid's default keeps one 50 MB file per container), then start "
               "Docker again."],
        link=UNRAID_DOCS)]


def check_extra_parameters(containers):
    findings = []
    restart = sorted(c["name"] for c in containers if "--restart" in (c.get("flags") or []))
    if restart:
        findings.append(finding(
            "restart", AREA, WARNING, "Unraid",
            f"{plural(len(restart), 'container')} {'uses' if len(restart) == 1 else 'use'} Docker's --restart instead of "
            "Unraid's Autostart",
            "A --restart policy in Extra Parameters makes Docker start the container as soon as Docker starts, before "
            "Unraid's own Autostart. It ignores your start order and wait times, so a container that goes through a "
            "VPN container can start before it, and it starts again even after you stop it for maintenance.",
            steps=["Edit each container below in Advanced View, remove --restart=... from Extra Parameters, and turn on "
                   "Autostart on the Docker page instead."],
            items=restart, link=GUIDE))
    for c in containers:
        if not c.get("privileged"):
            continue
        media = bool(c.get("role"))
        findings.append(finding(
            f"privileged|{c['name']}", AREA, WARNING if media else TIP, "Unraid", f"{c['name']} runs as Privileged",
            f"Privileged gives {c['name']} full control of Unraid: every disk, device and kernel setting. If the app "
            "is ever compromised, so is the server. "
            + ("Media apps never need it." if media else
               "Few containers need it; most only need one device or capability."),
            steps=[f"Edit {c['name']}, switch Privileged off (or remove --privileged from Extra Parameters).",
                   "If it then stops working, give it only what it needs, e.g. --device=/dev/dri for a GPU, or "
                   "--cap-add=NET_ADMIN and --device=/dev/net/tun for a VPN. Setuparr's Guides page lists these."],
            link=GUIDE))
    return findings


# Disks, parity, alerts and time

# Disk states Unraid shows in red or yellow on the Main page
BAD_DISK = {"DISK_DSBL": "is disabled, and its contents are being emulated from parity",
            "DISK_DSBL_NEW": "is disabled and waiting to be rebuilt",
            "DISK_INVALID": "has invalid contents and needs rebuilding",
            "DISK_WRONG": "isn't the disk that was assigned to this slot",
            "DISK_NP_MISSING": "is missing"}


def disk_label(disk):
    return {"parity": "Parity", "parity2": "Parity 2", "flash": "The flash drive"}.get(disk["name"]) or (
        f"Disk {disk['name'][4:]}" if re.fullmatch(r"disk\d+", disk["name"]) else f"The {disk['name']} pool device")


def as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def check_disks(disks):
    if not disks:
        return []
    findings = []
    for d in disks:
        label = disk_label(d)
        if d.get("status") in BAD_DISK:
            findings.append(finding(
                f"disk-state|{d['name']}", AREA, PROBLEM, "Unraid", f"{label} {BAD_DISK[d['status']]}",
                "Until it's fixed the array has no protection left: if another disk fails, its data is lost. Writes to "
                "an emulated disk are slow too.",
                steps=["Open Tools > Diagnostics and download them, then look at the disk's SMART report on the Main "
                       "page (click the disk).",
                       "If the disk is healthy (often it's a loose cable), rebuild onto it: stop the array, unassign the "
                       "disk, start the array, stop it, assign the disk again and start the array.",
                       "If it's failing, replace it and rebuild onto the new disk. Ask on the Unraid forum with your "
                       "diagnostics if you're not sure."],
                link=UNRAID_PARITY))
        if d.get("unmountable"):
            findings.append(finding(
                f"disk-unmountable|{d['name']}", AREA, PROBLEM, "Unraid", f"{label} is unmountable",
                "Unraid can't read the file system on it, so its files aren't in your shares. Don't format it: that "
                "erases the data and, on the array, updates parity to match.",
                steps=["Start the array in Maintenance mode, click the disk on the Main page and run Check Filesystem "
                       "Status without -n to repair it.",
                       "Ask on the Unraid forum with your diagnostics (Tools > Diagnostics) if the repair doesn't work."],
                link=UNRAID_DOCS))
    errors = sorted(f"{disk_label(d)}: {as_int(d.get('numErrors'))}" for d in disks if as_int(d.get("numErrors")) > 0)
    if errors:
        findings.append(finding(
            "disk-errors", AREA, WARNING, "Unraid", f"{plural(len(errors), 'disk')} had read or write errors",
            "The Errors column on the Main page isn't zero. It's often a loose or bad cable, but can be a failing "
            "disk. Unraid disables a disk when a write to it fails.",
            steps=["Click each disk on the Main page and look at its SMART attributes, especially Reallocated, Pending "
                   "and UDMA CRC errors. CRC errors mean the cable; the others mean the disk.",
                   "Reseat or replace the cable, then run a parity check. The counts reset when the server restarts."],
            items=errors, link=UNRAID_DOCS))

    data = [d for d in disks if d.get("type") == "Data" and d.get("status") not in ("DISK_NP", None)]
    parity = [d for d in disks if d.get("type") == "Parity" and d.get("status") not in ("DISK_NP", "DISK_NP_DSBL", None)]
    if data and not parity:
        findings.append(finding(
            "no-parity", AREA, WARNING, "Unraid", "Your array has no parity drive",
            "Without parity, a failed array disk loses everything on it. One parity drive protects against any one "
            "disk failing; it has to be at least as big as your biggest data disk.",
            steps=["Add a disk at least as big as your largest data disk, stop the array and assign it to the Parity "
                   "slot, then start the array to build parity."],
            items=[disk_label(d) for d in data], link=UNRAID_PARITY))

    names = {d["name"] for d in disks}
    for d in disks:
        size, free = as_int(d.get("fsSize")), as_int(d.get("fsFree"))
        member = re.sub(r"\d+$", "", d["name"]) != d["name"] and re.sub(r"\d+$", "", d["name"]) in names
        if d.get("type") not in ("Cache", "Flash") or member or size <= 0 or free / size > 0.1:
            continue
        flash = d.get("type") == "Flash"
        what = "The flash drive" if flash else f"The {d['name']} pool"
        findings.append(finding(
            f"full|{d['name']}", AREA, WARNING, "Unraid", f"{what} is {round(100 - free * 100 / size)}% full",
            ("Unraid saves its settings and updates to the flash drive. When it's full, settings don't save and "
             "updates fail." if flash else
             f"When {d['name']} fills up, downloads stop, containers' databases can be damaged, and Docker can stop "
             "working if it lives there."),
            steps=(["Delete old files from the flash drive's previous folder and any old diagnostics or logs saved "
                    "there, from the flash share or Main > Flash."] if flash else
                   ["Run the mover (Main > Move) if shares on this pool are set to move to the array.",
                    "Check which shares are filling it (Shares, Compute All), and give downloads shares a Minimum "
                    "free space so new files go to the array first."]),
            link=UNRAID_MOVER if not flash else UNRAID_DOCS))
    return findings


def check_parity_schedule(disks, dynamix):
    if not disks or not dynamix or dynamix.get("parity.mode") != "0":
        return []
    if not any(d.get("type") == "Parity" and d.get("status") == "DISK_OK" for d in disks):
        return []
    return [finding(
        "parity-schedule", AREA, WARNING, "Unraid", "Parity checks aren't scheduled",
        "A parity check reads every disk end to end. It finds disks that are going bad and parity that's wrong while "
        "you can still fix them, instead of when a disk fails and you need to rebuild. Unraid suggests once a month.",
        steps=["In Settings > Scheduler, set Parity Check to Monthly, at a time the server is quiet, and leave Write "
               "corrections to parity on No."],
        link=UNRAID_PARITY)]


def check_alerts(dynamix):
    """dynamix.cfg's notify settings add 1 for the browser, 2 for email and 4 for agents (Discord, Pushover...)."""
    if not dynamix:
        return []
    if as_int(dynamix.get("notify.alert") or "1") & 6:
        return []
    return [finding(
        "alerts", AREA, WARNING, "Unraid", "Unraid only tells you about failing disks in its web UI",
        "Alerts, like a disk being disabled or overheating, are only shown when you open Unraid's web UI. A failed "
        "disk can go unnoticed for weeks, and then a second failure loses data.",
        steps=["In Settings > Notification Settings, set up email (SMTP Settings) or a notification agent such as "
               "Discord, Pushover, Telegram or ntfy.",
               "Tick Email or Agents for Alerts and Warnings, and press Test to check it arrives."],
        link=UNRAID_NOTIFY)]


def check_time(ident, containers):
    findings = []
    if ident and ident.get("USE_NTP") == "no":
        findings.append(finding(
            "ntp", AREA, WARNING, "Unraid", "Unraid's clock isn't set from the internet",
            "Without NTP, the clock drifts. A clock that's minutes out breaks secure connections, logins with "
            "two-factor codes and indexer searches, and schedules run at the wrong time.",
            steps=["In Settings > Date and Time, set Time sync to NTP and keep the suggested time servers (or "
                   "pool.ntp.org), then Apply."],
            link=UNRAID_DOCS))
    differs = sorted(c["name"] for c in containers if c.get("tz_differs"))
    if differs:
        findings.append(finding(
            "timezone", AREA, TIP, "Unraid",
            f"{plural(len(differs), 'container')} {'runs' if len(differs) == 1 else 'run'} on a different time zone from Unraid",
            "Times in their logs, schedules and history don't match Unraid's, which makes problems hard to line up "
            "and can run tasks at the wrong hour. Compose containers without a TZ variable run on UTC.",
            steps=["For a Docker template: edit it and remove the TZ variable, so Unraid's time zone is used.",
                   "For a compose file: add TZ with your time zone (e.g. Europe/London) to each service's environment."],
            items=differs))
    return findings


def check_smb(shares):
    """appdata holds every app's API keys and databases, so it shouldn't be readable by anyone on the network."""
    findings = []
    for s in shares:
        if s["name"] != "appdata" or s.get("shareExport") in (None, "", "-"):
            continue
        if s.get("shareSecurity") in ("public", "secure"):
            findings.append(finding(
                "smb|appdata", AREA, WARNING, "Unraid", "Anyone on your network can read appdata",
                f"appdata is shared over SMB as {'Public' if s['shareSecurity'] == 'public' else 'Secure'}, so anyone "
                "on your network can open it without a password"
                + (" and change or delete files" if s["shareSecurity"] == "public" else "")
                + ". It holds every app's API keys, passwords and databases.",
                steps=["In Shares > appdata, set SMB Security Settings > Export to No, or set Security to Private and "
                       "give only your own user Read/Write access."],
                link=UNRAID_DOCS))
    return findings


# The mover and backups


def media_shares(media_containers):
    used = set()
    for c in media_containers:
        for m in c["data"]:
            where = location(m["host"])
            if where:
                used.add(where[1])
    return used


def check_mover(share_settings, shares, media_containers, disks):
    findings = []
    by_name = {s["name"]: s for s in shares}
    used = media_shares(media_containers)
    cron = ((share_settings or {}).get("shareMoverSchedule") or "").split()
    moving = [n for n in sorted(used) if by_name.get(n, {}).get("shareUseCache") == "yes"]
    if len(cron) == 5 and cron[1].startswith("*") and moving:
        findings.append(finding(
            "mover-hourly", AREA, TIP, "Unraid", "The mover runs every hour",
            "Each run spins up the array's disks, and moving files that are still being downloaded, seeded or "
            "imported slows everything down. Once a day, at night, is usually enough when the cache has room for a "
            "day's downloads.",
            steps=["In Settings > Scheduler > Mover Settings, set the mover schedule to Daily, at a quiet time."],
            items=moving, link=UNRAID_MOVER))
    for name in sorted(used):
        share = by_name.get(name)
        if not share:
            continue
        if share.get("shareUseCache") == "yes" and share.get("shareFloor") == "0":
            findings.append(finding(
                f"floor|{name}", AREA, WARNING, "Unraid", f"The {name} share has no minimum free space",
                f"New files in {name} go to the cache until it's completely full, and then a download or import fails "
                "half written. With a minimum free space, Unraid sends new files to the array once the cache gets low.",
                steps=[f"In Shares > {name}, set Minimum free space to a bit more than the largest file you download "
                       "(e.g. 100GB), or empty it to let Unraid work it out."],
                link=UNRAID_MOVER))
        array = disks is None or any(d.get("type") == "Data" and d.get("status") == "DISK_OK" for d in disks or [])
        if share.get("shareUseCache") == "only" and array:
            findings.append(finding(
                f"cache-only|{name}", AREA, WARNING, "Unraid", f"The {name} share never leaves the cache",
                f"{name} holds downloads or media, but it has no secondary storage, so the mover never moves it to the "
                "array. A library soon outgrows a cache pool, and when the pool's full, downloads fail. If this pool "
                "is meant to hold your whole library, dismiss this.",
                steps=[f"In Shares > {name}, set the secondary storage to the array and the mover action to Cache -> "
                       "Array (on Unraid before 6.12: Use cache pool: Yes)."],
                link=UNRAID_MOVER))
    return findings


def check_backup(backup, plugins, shares, containers):
    if not backup or (plugins is not None and not installed(plugins, "appdata.backup.plg", "appdata.backup.beta.plg")):
        return []
    findings = []
    if not backup["destination"]:
        return [finding(
            "backup-destination", AREA, WARNING, "Appdata Backup", "Appdata Backup has nowhere to save backups",
            "No destination is set, so it can't back anything up.",
            steps=["In Settings > Appdata Backup, choose a Backup destination on the array or another server, then "
                   "set a schedule."],
            link=APPDATA_BACKUP)]
    if backup["frequency"] == "disabled":
        findings.append(finding(
            "backup-schedule", AREA, WARNING, "Appdata Backup", "Appdata Backup never runs by itself",
            "Its schedule is Disabled, so backups only happen when you press Backup. The last backup is as old as the "
            "last time you remembered.",
            steps=["In Settings > Appdata Backup, set Scheduled backup frequency to Weekly (or Daily), at a quiet time."],
            link=APPDATA_BACKUP))
    if backup["flash"] == "no":
        findings.append(finding(
            "backup-flash", AREA, WARNING, "Appdata Backup", "The flash drive isn't backed up",
            "The flash drive holds your whole Unraid setup: disk assignments, shares, Docker templates and plugins. "
            "Flash drives fail, and without a backup you set everything up again by hand. Skip this if Unraid Connect "
            "backs it up for you.",
            steps=["In Settings > Appdata Backup, set Backup the flash drive? to Yes."],
            link=APPDATA_BACKUP))

    by_name = {s["name"]: s for s in shares}
    appdata_pool = by_name.get("appdata", {}).get("shareCachePool") or "cache"
    where = share_of(backup["destination"])
    same = False
    if where:
        kind, name = where
        dest = by_name.get(name, {})
        top = clean(backup["destination"]).split("/")[2]
        same = (name == "appdata" or (kind == "disk" and top == appdata_pool)
                or (kind == "user" and dest.get("shareUseCache") == "only" and (dest.get("shareCachePool") or "cache") == appdata_pool))
    if same:
        findings.append(finding(
            "backup-same-pool", AREA, WARNING, "Appdata Backup", "Appdata backups are on the same pool as appdata",
            f"Backups go to {backup['destination']}, which is on the {appdata_pool} pool with appdata itself. If the "
            "pool fails, you lose your apps and their backups together.",
            steps=["In Settings > Appdata Backup, choose a destination on the array (a share with no cache, or one the "
                   "mover moves to the array) or on another server."],
            items=[backup["destination"]], link=APPDATA_BACKUP))

    skipped = sorted(c["name"] for c in containers if c.get("role") and c["name"] in set(backup["skipped"]))
    if skipped:
        findings.append(finding(
            "backup-skipped", AREA, WARNING, "Appdata Backup",
            f"Appdata Backup skips {plural(len(skipped), 'media container')}",
            "Their settings and databases aren't backed up, so losing the cache pool means setting them up again and "
            "losing their history.",
            steps=["In Settings > Appdata Backup, set Skip? to No for each container below."],
            items=skipped, link=APPDATA_BACKUP))
    return findings


# Plugins and drivers


def size_text(n):
    for unit in ("TB", "GB", "MB"):
        if n >= 1000 ** " KMGT".index(unit[0]):
            return f"{n / 1000 ** ' KMGT'.index(unit[0]):.1f} {unit}"
    return f"{n} bytes"


def unused_appdata(appdata, containers, installed=()):
    """Folders in appdata that no container maps, and that aren't named after an installed one (which may have no
    template). None if a container maps the whole of appdata, since then every folder looks used."""
    used = {n.lower() for n in [c["name"] for c in containers] + list(installed)}
    for c in containers:
        for m in c["mounts"]:
            match = APPDATA.match(clean(m["host"]))
            if match and not match.group(1):
                return None
            if match:
                used.add(match.group(1).lower())
    return [n for n in appdata if n.lower() not in used]


def check_cleanup(snapshot):
    """Leftovers from removed containers: appdata folders, templates, and Docker's unused images and volumes."""
    findings = []
    containers = snapshot["containers"]
    unused = unused_appdata(snapshot.get("appdata") or [], containers, snapshot.get("installed") or ())
    if unused:
        findings.append(finding(
            "appdata-unused", AREA, TIP, "Unraid",
            f"{plural(len(unused), 'appdata folder doesn', 'appdata folders don')}'t belong to any container",
            "No installed container maps " + ("this folder" if len(unused) == 1 else "these folders") + ", so they're "
            "probably left over from containers you've removed. They take up space on your cache"
            + (", and Appdata Backup still backs them up." if snapshot.get("backup") else "."),
            steps=["Check each one first. A folder can belong to a plugin, or to a compose container that maps it by "
                   "a relative path.",
                   "Move them out rather than deleting them straight away. In Unraid's terminal, for each one, run: "
                   "mkdir -p /mnt/user/appdata-removed && mv \"/mnt/user/appdata/NAME\" /mnt/user/appdata-removed/",
                   "Once everything has worked for a week or two, delete /mnt/user/appdata-removed."],
            items=unused))
    templates = snapshot.get("unused_templates") or []
    if templates:
        findings.append(finding(
            "templates-unused", AREA, TIP, "Unraid",
            f"Unraid still has templates for {plural(len(templates), 'container')} you've removed",
            "Unraid keeps the template of every container you've installed, so you can reinstall it with the same "
            "settings. Setuparr leaves these out of its checks, but they fill the template list in Add Container.",
            steps=["In Unraid, go to Apps > Previous Apps and remove the ones you won't reinstall."],
            items=templates))
    docker = snapshot.get("docker_unused") or {}
    images, volumes = docker.get("images", 0), docker.get("volumes", 0)
    if images + volumes >= DOCKER_UNUSED_BYTES:
        steps = []
        if images:
            steps.append("In Unraid's terminal, run docker image prune -a to delete the images no container uses. "
                         "Images of stopped containers are kept.")
        if volumes:
            steps.append("Run docker volume prune to delete the volumes no container uses.")
        findings.append(finding(
            "docker-unused", AREA, TIP, "Unraid", f"Docker is keeping {size_text(images + volumes)} it doesn't use",
            "These are old versions left behind by updates and the images and volumes of containers you've removed"
            + (f" ({size_text(images)} of images, {size_text(volumes)} of volumes)" if images and volumes else "")
            + ". They fill docker.img or the Docker folder, and when that's full, containers stop.",
            steps=steps))
    return findings


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


def check_plugin_versions(plugins, version):
    if not plugins or not version:
        return []
    current = version_tuple(version)
    wrong = []
    for p in plugins:
        if p.get("max") and current[:len(version_tuple(p["max"]))] > version_tuple(p["max"]):
            wrong.append(f"{p['file']}: made for Unraid {p['max']} and earlier")
        elif p.get("min") and current < version_tuple(p["min"]):
            wrong.append(f"{p['file']}: needs Unraid {p['min']} or later")
    if not wrong:
        return []
    return [finding(
        "plugin-version", AREA, WARNING, "Unraid", f"{plural(len(wrong), 'plugin')} {'isn' if len(wrong) == 1 else 'aren'}'t made for Unraid {version}",
        "Unraid's plugin manager won't update these, and they can break the web UI or stop the server booting "
        "cleanly. Usually there's a newer version, or Unraid now does the job itself.",
        steps=["Open Plugins and press Check for Updates.",
               "If there's no update, remove the plugin, then look in Apps for a replacement that supports your Unraid "
               "version."],
        items=wrong)]


GPU_CLASSES = ("0300", "0302", "0380")
GPU_VENDORS = {"8086": "Intel", "1002": "AMD", "10de": "Nvidia"}
REALTEK_DRIVERS = [
    # (PCI IDs, plugin files, driver) from Fix Common Problems' Realtek check
    ({"10ec:8125", "10ec:5000", "10ec:3000", "10ec:8162"}, ("unraid-r8125.plg",), "RTL8125"),
    ({"10ec:8126"}, ("unraid-r8126.plg", "unraid-r8125.plg"), "RTL8126"),
    ({"10ec:8168", "10ec:8161", "10ec:2502", "10ec:2600", "1186:4300", "1186:4b10"}, ("unraid-r8168.plg",), "RTL8168"),
]
CORAL = {"1ac1:089a"}


def check_hardware(hardware, plugins, servers, containers=()):
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

    displays = any(d["class"].startswith("03") for d in hardware)
    for c in containers:
        gpu = c.get("gpu") or {}
        if gpu.get("nvidia") and "Nvidia" not in gpus:
            findings.append(finding(
                f"gpu-missing-nvidia|{c['name']}", AREA, PROBLEM, "Unraid", f"{c['name']} is given an Nvidia GPU this server doesn't have",
                f"{c['name']} runs with the Nvidia runtime and NVIDIA_VISIBLE_DEVICES, but there's no Nvidia GPU here, "
                "so Docker can't start it.",
                steps=[f"Edit {c['name']} in Advanced View, remove --runtime=nvidia from Extra Parameters and remove the "
                       "NVIDIA_VISIBLE_DEVICES and NVIDIA_DRIVER_CAPABILITIES variables."]))
        if gpu.get("dri") and not ({"Intel", "AMD"} & set(gpus)):
            findings.append(finding(
                f"gpu-missing-dri|{c['name']}", AREA, WARNING, "Unraid", f"{c['name']} is given /dev/dri, but there's no Intel or AMD GPU",
                f"{c['name']} is given /dev/dri for hardware transcoding, but this server has no Intel or AMD GPU for "
                "it to use. " + ("A server board's own graphics shows up as /dev/dri too, but can't transcode."
                                 if displays else "Without one, /dev/dri doesn't exist and Docker can't start it."),
                steps=[f"Edit {c['name']} and remove the /dev/dri device (or --device=/dev/dri from Extra Parameters).",
                       "If the CPU has graphics, check it's turned on in the BIOS (often called iGPU or Multi-Monitor)."]))

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
            "disks": len(snapshot["disks"]) if "disks" in snapshot else None,
            "autostart": len(snapshot["autostart"]) if "autostart" in snapshot else None,
            "backup": "backup" in snapshot,
            "unraid_version": snapshot.get("unraid_version"),
            "unused_templates": len(snapshot["unused_templates"]) if "unused_templates" in snapshot else None,
            "appdata": len(snapshot["appdata"]) if "appdata" in snapshot else None}
