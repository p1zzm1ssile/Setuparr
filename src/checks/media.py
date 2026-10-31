"""Plex, Jellyfin and Emby's own settings: hardware transcoding, where transcodes are written, noticing new files,
failed scheduled tasks, accounts without a password and database backups. Whether the containers are given a GPU is
checked with the Unraid templates (unraid.check_hardware)."""

from checks import PROBLEM, TIP, WARNING, finding, plural, unraid

AREA = "media"
SERVERS = {"plex": "Plex", "jellyfin": "Jellyfin", "emby": "Emby"}
DOCS = {
    "plex": {"hardware": "https://support.plex.tv/articles/115002178853-using-hardware-accelerated-streaming/",
             "lan": "https://support.plex.tv/articles/200430283-network/",
             "scan": "https://support.plex.tv/articles/200289306-scanning-vs-refreshing-a-library/"},
    "jellyfin": {"hardware": "https://jellyfin.org/docs/general/post-install/transcoding/hardware-acceleration/",
                 "scan": "https://jellyfin.org/docs/general/server/libraries/"},
    "emby": {"hardware": "https://emby.media/support/articles/Hardware-Acceleration-Overview.html",
             "scan": "https://emby.media/support/articles/Library-Setup.html"},
}


def check(key, d, snapshot=None):
    label = SERVERS[key]
    container = server_container(snapshot, label)
    if key == "plex":
        return check_plex(d.get("prefs") or {}, container)
    return check_jellyfin(key, label, d, container)


def server_container(snapshot, label):
    """The one Unraid container running this server, or None."""
    if not snapshot:
        return None
    ignored = set(snapshot.get("ignored", []))
    found = [c for c in snapshot["containers"] if c["name"] not in ignored and unraid.role(c) == ("server", label)]
    return found[0] if len(found) == 1 else None


def has_gpu(container):
    gpu = (container or {}).get("gpu") or {}
    return bool(gpu.get("dri") or gpu.get("nvidia"))


def hardware_off(key, label, container, where):
    """Hardware transcoding is off. Only worth saying when the container has a GPU, or when there's no Unraid
    snapshot to tell."""
    if container and not has_gpu(container):
        return []
    detail = (f"{container['name']} is given a GPU, but {label} doesn't use it" if container
              else f"{label} doesn't use a GPU") + (
        ", so every transcode runs on the CPU. A few streams at once can max it out, and 4K or HDR transcodes may "
        "buffer.")
    return [finding(
        "hardware-off", AREA, WARNING if container else TIP, label, f"{label} transcodes without the GPU", detail,
        steps=[where], link=DOCS[key]["hardware"])]


def transcode_unmapped(label, path, container):
    """A transcode folder that isn't in any of the container's mappings is inside the container, so in docker.img."""
    if not path or not container or not str(path).startswith("/"):
        return []
    clean = unraid.clean(str(path))
    if any(unraid.inside(clean, unraid.clean(m["container"])) for m in container["mounts"]):
        return []
    return [finding(
        "transcode-unmapped", AREA, WARNING, label, f"{label} writes transcodes inside its container",
        f"The transcode folder is {path}, which isn't in any of {container['name']}'s folder mappings. Transcodes then "
        "go into Docker's own storage (docker.img), which can fill up and stop every container.",
        steps=[f"Map a folder for transcodes in {container['name']}'s template (e.g. /tmp or a share on your cache, to "
               f"/transcode), then set {label}'s transcode folder to /transcode."],
        items=[str(path)], link=unraid.GUIDE)]


def check_plex(prefs, container):
    findings = []
    if prefs.get("HardwareAcceleratedCodecs") is False:
        findings += hardware_off("plex", "Plex", container,
                                 "In Plex, go to Settings > Transcoder, turn on Use hardware acceleration when available "
                                 "(it needs Plex Pass) and choose your GPU.")
    findings += transcode_unmapped("Plex", prefs.get("TranscoderTempDirectory"), container)
    if prefs.get("FSEventLibraryUpdatesEnabled") is False:
        findings.append(finding(
            "no-auto-scan", AREA, TIP, "Plex", "Plex doesn't notice new downloads straight away",
            "Scan my library automatically is off, so new episodes and films only appear after the next periodic scan"
            + (", and periodic scans are off too." if prefs.get("ScheduledLibraryUpdatesEnabled") is False else "."),
            steps=["In Plex, go to Settings > Library and turn on Scan my library automatically.",
                   "Or, in Sonarr and Radarr, add Plex Media Server under Settings > Connect so they tell Plex about "
                   "each import."],
            link=DOCS["plex"]["scan"]))
    if container and container.get("network") == "bridge" and prefs.get("LanNetworks") in ("", None):
        findings.append(finding(
            "lan-networks", AREA, TIP, "Plex", "Plex may treat your home devices as remote",
            f"{container['name']} uses Docker's bridge network, so Plex sees your devices coming from Docker's address "
            "rather than your home network. It can then count them as remote streams and limit their quality.",
            steps=["In Plex, go to Settings > Network (Show Advanced) and set LAN Networks to your home network, e.g. "
                   "192.168.1.0/24."],
            link=DOCS["plex"]["lan"]))
    if prefs.get("ButlerTaskBackupDatabase") is False:
        findings.append(finding(
            "no-backup", AREA, TIP, "Plex", "Plex doesn't back up its database",
            "Back up database every three days is off, so if Plex's database is damaged (a full cache pool or a power "
            "cut) you lose your watch history and have to scan everything again.",
            steps=["In Plex, go to Settings > Scheduled Tasks and turn on Back up database every three days."]))
    return findings


def check_jellyfin(key, label, d, container):
    findings = []
    encoding = d.get("encoding") or {}
    if "HardwareAccelerationType" in encoding and encoding["HardwareAccelerationType"] in ("", "none", None):
        findings += hardware_off(key, label, container,
                                 f"In {label}, go to Dashboard > Playback > Transcoding, choose Hardware acceleration "
                                 "(Intel QuickSync or VAAPI for Intel and AMD, NVENC for Nvidia) and tick the codecs "
                                 "your GPU can decode.")
    findings += transcode_unmapped(label, encoding.get("TranscodingTempPath"), container)

    unwatched = [l["name"] or "?" for l in d.get("libraries") or [] if l.get("monitor") is False]
    if unwatched:
        findings.append(finding(
            "no-monitor", AREA, TIP, label, f"{label} doesn't notice new files in {plural(len(unwatched), 'library', 'libraries')}",
            "Real time monitoring is off, so new episodes and films only appear after the next scheduled library scan.",
            steps=[f"In {label}, go to Dashboard > Libraries, edit each library and turn on Enable real time monitoring.",
                   f"Or, in Sonarr and Radarr, add {label} under Settings > Connect so they tell it about each import."],
            items=unwatched, link=DOCS[key]["scan"]))

    failed = [t["name"] or "?" for t in d.get("tasks") or [] if t.get("status") == "Failed"]
    if failed:
        findings.append(finding(
            "tasks-failed", AREA, WARNING, label, f"{plural(len(failed), 'scheduled task')} failed last time in {label}",
            "These tasks failed the last time they ran. A failing library scan means new media doesn't show up.",
            steps=[f"In {label}, go to Dashboard > Scheduled Tasks, run the task again and check {label}'s log for "
                   "the error."],
            items=failed))

    users = d.get("users") or []
    open_admins = [u["name"] or "?" for u in users if u.get("admin") and u.get("password") is False]
    open_users = [u["name"] or "?" for u in users if not u.get("admin") and u.get("password") is False]
    if open_admins:
        findings.append(finding(
            "admin-no-password", AREA, PROBLEM, label, f"An administrator in {label} has no password",
            f"Anyone who can reach {label} can sign in as these accounts and change any setting, or delete media if "
            "deleting is allowed. If it's reachable from the internet, that's anyone.",
            steps=[f"In {label}, go to Dashboard > Users, open each account, choose Password and set one."],
            items=open_admins))
    if open_users:
        findings.append(finding(
            "user-no-password", AREA, TIP, label, f"{plural(len(open_users), 'account')} in {label} {'has' if len(open_users) == 1 else 'have'} no password",
            f"Anyone who can reach {label} can watch as these accounts. That's fine at home, but not if {label} is "
            "reachable from the internet.",
            steps=[f"In {label}, go to Dashboard > Users, open each account, choose Password and set one."],
            items=open_users))
    if (d.get("info") or {}).get("HasPendingRestart"):
        findings.append(finding(
            "restart", AREA, TIP, label, f"{label} is waiting to be restarted",
            f"A change or update in {label} only takes effect after a restart.",
            steps=[f"Restart {label} from its Dashboard, or restart its container, when nobody's watching."]))
    return findings
