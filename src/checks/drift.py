"""Sonarr and Radarr against the TRaSH guide itself: profiles named after a TRaSH profile that no longer match it,
quality sizes, and naming.

guide is what services.trash.guide downloads: TRaSH's own JSON files, kept for a day.
"""

from checks import TIP, WARNING, finding, plural
from services import trash

AREA = "trash"
LINKS = {
    "sonarr": {"profiles": "https://trash-guides.info/Sonarr/sonarr-setup-quality-profiles/",
               "sizes": "https://trash-guides.info/Sonarr/Sonarr-Quality-Settings-File-Size/",
               "naming": "https://trash-guides.info/Sonarr/Sonarr-recommended-naming-scheme/"},
    "radarr": {"profiles": "https://trash-guides.info/Radarr/radarr-setup-quality-profiles/",
               "sizes": "https://trash-guides.info/Radarr/Radarr-Quality-Settings-File-Size/",
               "naming": "https://trash-guides.info/Radarr/Radarr-recommended-naming-scheme/"},
}
# The quality sizes for ordinary series and films. Anime has its own, but they apply to the whole app, so only
# these are compared.
SIZE_TYPES = {"sonarr": "series", "radarr": "movie"}
SIZE_FIELDS = (("min", "minSize", "Minimum"), ("preferred", "preferredSize", "Preferred"), ("max", "maxSize", "Maximum"))
# Media servers, in the order a naming scheme is suggested for, and the key TRaSH's variants start with
SERVERS = (("plex", "Plex"), ("jellyfin", "Jellyfin"), ("emby", "Emby"))


def check(service, label, d, guide, servers=()):
    """d holds what fetch_arr reads. servers: the media servers connected to Setuparr, to suggest a naming scheme."""
    if service not in LINKS or not guide:
        return []
    following = trash_profiles_in_use(d, guide)
    return (check_profiles(service, label, d, guide)
            + (check_sizes(service, label, d.get("definitions"), guide) if following else [])
            + check_naming(service, label, d.get("naming"), guide, servers))


def _by_name(guide):
    return {str(p.get("name", "")).lower(): p for p in guide.get("quality-profiles") or []
            if p.get("visible") != "private"}


def trash_profiles_in_use(d, guide):
    """Whether this app follows TRaSH: one of its profiles has a TRaSH profile's name."""
    names = _by_name(guide)
    return any(str(p.get("name", "")).lower() in names for p in d.get("profiles") or [])


def cutoff_name(profile):
    for item in profile.get("items") or []:
        if item.get("items") and item.get("id") == profile.get("cutoff"):
            return item.get("name")
        if (item.get("quality") or {}).get("id") == profile.get("cutoff") and not item.get("items"):
            return item["quality"].get("name")
    return None


def profile_differences(profile, ref, formats, guide):
    """What's different between a profile and the TRaSH profile it's named after, as plain lines."""
    by_name = {str(f.get("name", "")).lower(): f for f in formats or []}
    scores = {f.get("format"): f.get("score") or 0 for f in profile.get("formatItems") or []}
    lines = []
    for cf, score in trash.selected_formats(guide, ref):
        mine = by_name.get(cf["name"].lower())
        if not mine:
            lines.append(f"Custom format {cf['name']} is missing (TRaSH scores it {score})")
            continue
        if not trash.same_format(mine, trash.format_body(cf)):
            lines.append(f"Custom format {cf['name']} doesn't match TRaSH's any more")
        if scores.get(mine.get("id"), 0) != score:
            lines.append(f"Custom format {cf['name']} scores {scores.get(mine.get('id'), 0)} here, {score} in TRaSH")
    if (profile.get("minFormatScore") or 0) != ref.get("minFormatScore", 0):
        lines.append(f"Minimum Custom Format Score is {profile.get('minFormatScore') or 0} here, "
                     f"{ref.get('minFormatScore', 0)} in TRaSH")
    if bool(profile.get("upgradeAllowed")) != bool(ref.get("upgradeAllowed", True)):
        lines.append(f"Upgrades Allowed is {'on' if profile.get('upgradeAllowed') else 'off'} here, "
                     f"{'on' if ref.get('upgradeAllowed', True) else 'off'} in TRaSH")
    mine = cutoff_name(profile)
    if mine and ref.get("cutoff") and mine.lower() != str(ref["cutoff"]).lower():
        lines.append(f"Upgrade Until is {mine} here, {ref['cutoff']} in TRaSH")
    return lines


def check_profiles(service, label, d, guide):
    formats = d.get("formats")
    if formats is None:  # Sonarr v3: no custom formats to compare
        return []
    names = _by_name(guide)
    used = {}
    for item in d.get("items") or []:
        used.setdefault(item.get("qualityProfileId"), 0)
        used[item.get("qualityProfileId")] += 1
    findings = []
    for p in d.get("profiles") or []:
        ref = names.get(str(p.get("name", "")).lower())
        if not ref:
            continue
        lines = profile_differences(p, ref, formats, guide)
        if not lines:
            continue
        name, count = p.get("name"), used.get(p.get("id"), 0)
        missing = any("is missing" in line for line in lines)
        findings.append(finding(
            f"drift|{p.get('id')}", AREA, WARNING if missing else TIP, label,
            f"The {name} profile in {label} no longer matches TRaSH's",
            f"It has the name of a TRaSH profile, but {plural(len(lines), 'thing differs', 'things differ')} from the "
            f"guide as it is today. TRaSH updates its scores and custom formats as new release groups and formats "
            f"appear, so a profile set up once slowly goes out of date."
            + (f" {plural(count, 'item uses', 'items use')} it." if count else ""),
            steps=["Press Update to TRaSH to bring it up to date. Scores you gave your own custom formats are kept, "
                   "nothing is deleted, and it can be undone under Recent fixes.",
                   "If you changed it on purpose, dismiss this. Or give the profile your own name, so it isn't "
                   "compared any more."],
            items=lines, link=ref.get("trash_url") or LINKS[service]["profiles"],
            fix={"action": "trash_import", "params": {"app": service, "trash_id": ref["trash_id"]},
                 "label": "Update to TRaSH",
                 "confirm": f"Update the {name} profile and its custom formats in {label} to match TRaSH? You can "
                            "undo it under Recent fixes."}))
    return findings


def size_reference(service, guide):
    wanted = SIZE_TYPES[service]
    return next((s for s in guide.get("quality-size") or [] if s.get("type") == wanted), None)


def size_differences(definitions, ref):
    """{quality name: [(label, yours, TRaSH's)]} for the qualities TRaSH sets. An empty value (unlimited) in the app
    isn't compared, since the sliders show it as their top end."""
    mine = {(d.get("quality") or {}).get("name"): d for d in definitions or []}
    out = {}
    for q in ref.get("qualities") or []:
        current = mine.get(q.get("quality"))
        if not current:
            continue
        diffs = []
        for key, field, word in SIZE_FIELDS:
            value = current.get(field)
            if value is None or q.get(key) is None:
                continue
            if abs(float(value) - float(q[key])) > 0.05:
                diffs.append((word, value, q[key]))
        if diffs:
            out[q["quality"]] = diffs
    return out


def _size(value):
    return f"{float(value):g}"


def check_sizes(service, label, definitions, guide):
    ref = size_reference(service, guide)
    if not ref or not definitions:
        return []
    diffs = size_differences(definitions, ref)
    if not diffs:
        return []
    lines = [f"{quality}: " + ", ".join(f"{word} {_size(a)} here, {_size(b)} in TRaSH" for word, a, b in found)
             for quality, found in diffs.items()]
    return [finding(
        "sizes", AREA, TIP, label, f"{plural(len(diffs), 'quality size')} in {label} {'differs' if len(diffs) == 1 else 'differ'} from TRaSH's",
        f"You use TRaSH profiles, but not TRaSH's quality sizes. The sizes (MB per minute) decide which releases are "
        f"too small or too big to grab, and TRaSH's are tuned to drop fakes and bloated encodes without losing good "
        "releases.",
        steps=["Press Use TRaSH sizes to set them. It can be undone under Recent fixes.",
               f"Or set them by hand in {label} under Settings > Quality (Show Advanced)."],
        items=lines, link=LINKS[service]["sizes"],
        fix={"action": "set_quality_sizes", "params": {"service": service, "trash_id": ref.get("trash_id")},
             "label": "Use TRaSH sizes",
             "confirm": f"Set {plural(len(diffs), 'quality size')} in {label} to TRaSH's? You can undo it under Recent "
                        "fixes."})]


def naming_fields(service, naming_guide):
    """[(setting in the API, its name on screen, TRaSH's variants {name: format})] to compare."""
    if service == "radarr":
        return [("standardMovieFormat", "Standard Movie Format", naming_guide.get("file") or {}),
                ("movieFolderFormat", "Movie Folder Format", naming_guide.get("folder") or {})]
    episodes = naming_guide.get("episodes") or {}
    # Anime naming has its own check (anime.py), which also covers the absolute episode number
    return [("standardEpisodeFormat", "Standard Episode Format", episodes.get("standard") or {}),
            ("dailyEpisodeFormat", "Daily Episode Format", episodes.get("daily") or {}),
            ("seriesFolderFormat", "Series Folder Format", naming_guide.get("series") or {}),
            ("seasonFolderFormat", "Season Folder Format", naming_guide.get("season") or {})]


def suggested(variants, servers):
    """TRaSH's format for the first connected media server, or its default."""
    for key, _ in SERVERS:
        if key in servers:
            match = next((v for k, v in variants.items() if k.startswith(f"{key}-") and "anime" not in k
                          and "alt" not in k), None)
            if match:
                return match
    return variants.get("default") or variants.get("standard") or next(iter(variants.values()), "")


def _same(a, b):
    return " ".join(str(a or "").split()) == " ".join(str(b or "").split())


def check_naming(service, label, naming, guide, servers=()):
    rename = {"sonarr": "renameEpisodes", "radarr": "renameMovies"}[service]
    naming_guide = guide.get("naming")
    # Renaming turned off has its own finding (library.py)
    if not naming or not naming_guide or naming.get(rename) is False:
        return []
    lines, steps = [], []
    for field, name, variants in naming_fields(service, naming_guide):
        if not variants or field not in naming:
            continue
        if any(_same(naming[field], v) for v in variants.values()):
            continue
        lines.append(name)
        steps.append(f"{name}: {suggested(variants, servers)}")
    if not lines:
        return []
    server = next((label for key, label in SERVERS if key in servers), None)
    return [finding(
        "naming", AREA, TIP, label, f"{label}'s naming doesn't follow TRaSH's",
        f"{plural(len(lines), 'naming setting')} {'is' if len(lines) == 1 else 'are'} none of TRaSH's formats. TRaSH's "
        "put the quality, edition and release group in each name, so nothing is lost if the database is, and "
        + (f"its {server} versions add the ID {server} uses to match files." if server else
           "the versions for Plex, Jellyfin and Emby add the ID they use to match files."),
        steps=[f"In {label}, go to Settings > Media Management (Show Advanced) and paste in TRaSH's format"
               + (f" for {server}" if server else "") + ":", *steps,
               "Renaming existing files is separate: Library > Select > Organize. Only do it once you're happy."],
        items=lines, link=LINKS[service]["naming"])]
