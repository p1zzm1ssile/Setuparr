"""Sonarr and Radarr's library settings: root folders, naming, and media management, against the TRaSH guides."""

from checks import PROBLEM, TIP, WARNING, finding, plural

AREA = "library"
TRASH = {
    "sonarr": {"naming": "https://trash-guides.info/Sonarr/Sonarr-recommended-naming-scheme/",
               "repack": "https://trash-guides.info/Sonarr/sonarr-collection-of-custom-formats/#repackproper"},
    "radarr": {"naming": "https://trash-guides.info/Radarr/Radarr-recommended-naming-scheme/",
               "repack": "https://trash-guides.info/Radarr/Radarr-collection-of-custom-formats/#repackproper"},
    # TRaSH has no Lidarr guide
    "lidarr": {"naming": "https://wiki.servarr.com/lidarr/settings#track-naming",
               "repack": "https://wiki.servarr.com/lidarr/settings#file-management"},
}
TRASH_HARDLINKS = "https://trash-guides.info/File-and-Folder-Structure/Hardlinks-and-Instant-Moves/"
PROPERS = {"preferAndUpgrade": "Prefer and Upgrade", "doNotUpgrade": "Do not Upgrade Automatically"}
RENAME = {"sonarr": ("renameEpisodes", "Rename Episodes", "episodes"), "radarr": ("renameMovies", "Rename Movies", "films"),
          "lidarr": ("renameTracks", "Rename Tracks", "tracks")}


def check(service, label, d):
    """d holds roots, naming, media (config/mediamanagement) and formats (customformat), when fetched."""
    findings = []

    missing = [r for r in d.get("roots") or [] if r.get("accessible") is False]
    if missing:
        findings.append(finding(
            "roots-missing", AREA, PROBLEM, label,
            f"{label} can't reach {plural(len(missing), 'root folder')}",
            f"{label} can't import into, or find, anything in a root folder it can't reach. Usually the folder "
            f"mapping in {label}'s container changed, or the share or disk isn't there.",
            steps=[f"Check {label}'s folder mappings, then in {label} go to Settings > Media Management > Root Folders "
                   "and fix or remove the folders below."],
            items=[r.get("path", "?") for r in missing], link=TRASH_HARDLINKS))

    media = d.get("media") or {}
    if media.get("copyUsingHardlinks") is False:
        findings.append(finding(
            "hardlinks-off", AREA, WARNING, label,
            f"{label} copies downloads instead of hardlinking them",
            "Use Hardlinks instead of Copy is off, so every import is a full copy, and anything still seeding "
            "takes up its space twice.",
            steps=[f"In {label}, go to Settings > Media Management (Show Advanced) and turn on Use Hardlinks instead of Copy."],
            link=TRASH_HARDLINKS))

    repack_formats = [f["name"] for f in d.get("formats") or []
                      if any(word in f.get("name", "").lower() for word in ("repack", "proper"))]
    propers = media.get("downloadPropersAndRepacks")
    if repack_formats and propers not in (None, "doNotPrefer"):
        findings.append(finding(
            "repacks", AREA, WARNING, label,
            f"{label}'s Propers and Repacks setting works against your Repack/Proper custom format",
            f"Propers and Repacks is set to {PROPERS.get(propers, propers)}, so {label} prefers them before custom "
            "formats are scored. The TRaSH guide sets it to Do Not Prefer and leaves it to the custom format.",
            steps=[f"In {label}, go to Settings > Media Management > File Management and set Propers and Repacks to "
                   "Do Not Prefer."],
            items=repack_formats, link=TRASH[service]["repack"]))

    key, setting, things = RENAME[service]
    if (d.get("naming") or {}).get(key) is False:
        findings.append(finding(
            "rename-off", AREA, TIP, label,
            f"{label} keeps the downloaded file names",
            f"{setting} is off, so your {things} keep whatever name the release had. The TRaSH naming scheme puts "
            "the quality, edition and release group in the name, so nothing is lost if the database is, and "
            "Jellyfin and Plex match files more reliably.",
            steps=[f"In {label}, go to Settings > Media Management, turn on {setting} and use the TRaSH naming scheme."],
            link=TRASH[service]["naming"]))
    return findings
