"""Anime in Sonarr and Seerr.

Anime needs Sonarr's Anime series type to search with absolute episode
numbers, its own naming with the absolute number, and indexers that search
the anime category. Most people also keep it in its own root folder and
quality profile.
"""

from checks import PROBLEM, TIP, TRASH_SONARR_ANIME, TRASH_SONARR_NAMING, WARNING, finding, plural

AREA = "anime"


def kind(series):
    """"anime" when TVDB calls it anime, "likely" for Japanese animation, else None."""
    genres = {g.lower() for g in series.get("genres") or []}
    if "anime" in genres:
        return "anime"
    language = ((series.get("originalLanguage") or {}).get("name") or "").lower()
    if "animation" in genres and language == "japanese":
        return "likely"
    return None


def root_of(series, roots):
    path = series.get("rootFolderPath") or series.get("path", "")
    matches = [r for r in roots if path == r or path.startswith(r.rstrip("/") + "/")]
    return max(matches, key=len) if matches else path.rstrip("/").rsplit("/", 1)[0]


def anime_category_count(indexer):
    for field in indexer.get("fields") or []:
        if field.get("name") == "animeCategories":
            return len(field.get("value") or [])
    return 0


def type_fix(series, wanted):
    return {"action": "set_series_type",
            "params": {"series_ids": [s["id"] for s in series], "type": wanted},
            "label": "Set to Anime",
            "confirm": f"Set {plural(len(series), 'series', 'series')} to the Anime series type in Sonarr? Sonarr will "
                       "search with absolute episode numbers and use your anime naming for new files. Setuparr keeps "
                       "the old setting so you can undo this."}


def check(sonarr, seerr=None):
    """sonarr holds items (series), roots, profiles, naming and indexers. seerr holds servers, or None."""
    series = sonarr["items"]
    findings = []
    anime_typed = [s for s in series if s.get("seriesType") == "anime"]

    wrong = [s for s in series if kind(s) == "anime" and s.get("seriesType") != "anime"]
    if wrong:
        findings.append(finding(
            "type", AREA, PROBLEM, "Sonarr",
            f"{plural(len(wrong), 'anime series', 'anime series')} not set to the Anime series type",
            "TVDB lists these as anime, but Sonarr treats them as normal shows. Anime releases are numbered by "
            "absolute episode, so Sonarr searches for the wrong numbers and often finds nothing or the wrong episode.",
            steps=["In Sonarr, edit each series and set Series Type to Anime, or press Set to Anime.",
                   "To catch new ones, set Series Type to Anime when adding anime (Seerr can do this for you)."],
            items=[s["title"] for s in wrong], link=TRASH_SONARR_ANIME, fix=type_fix(wrong, "anime")))

    maybe = [s for s in series if kind(s) == "likely" and s.get("seriesType") != "anime"]
    if maybe:
        findings.append(finding(
            "type-maybe", AREA, TIP, "Sonarr",
            f"{plural(len(maybe), 'series', 'series')} may be anime",
            "These are Japanese animation, but TVDB doesn't call them anime, so Setuparr won't change them by itself. "
            "If they're released like anime (absolute episode numbers, fansub groups), set them to Anime.",
            steps=["In Sonarr, edit the series and set Series Type to Anime if it fits."],
            items=[s["title"] for s in maybe], link=TRASH_SONARR_ANIME))

    odd = [s for s in anime_typed if kind(s) is None]
    if odd:
        findings.append(finding(
            "type-odd", AREA, WARNING, "Sonarr",
            f"{plural(len(odd), 'series', 'series')} set to Anime that don't look like anime",
            "These use the Anime series type, but TVDB doesn't list them as anime or Japanese animation. Normal shows "
            "set to Anime are searched by absolute episode number, which usually finds nothing.",
            steps=["In Sonarr, edit each series and set Series Type to Standard, unless it really is anime."],
            items=[s["title"] for s in odd], link=TRASH_SONARR_ANIME))

    findings += check_folders(series, sonarr["roots"])
    findings += check_profiles(series, sonarr["profiles"])

    if anime_typed:
        naming = sonarr.get("naming") or {}
        anime_format = naming.get("animeEpisodeFormat") or ""
        if "absolute" not in anime_format.lower():
            findings.append(finding(
                "naming", AREA, WARNING, "Sonarr",
                "Sonarr's anime episode format has no absolute episode number",
                "Anime is released and matched by absolute episode number. Without it in the file name, Jellyfin, "
                "Plex and Sonarr itself can match episodes wrongly.",
                steps=["In Sonarr, go to Settings > Media Management > Episode Naming.",
                       "Change Anime Episode Format to the one in the TRaSH guide, which includes {absolute:000}."],
                items=[anime_format] if anime_format else [], link=TRASH_SONARR_NAMING))

        active = [i for i in sonarr["indexers"] if i.get("enableRss") or i.get("enableAutomaticSearch")]
        if active and not any(anime_category_count(i) for i in active):
            findings.append(finding(
                "indexers", AREA, PROBLEM, "Sonarr",
                "None of Sonarr's indexers search the anime category",
                "Anime series are searched only in each indexer's anime categories, and none of your indexers have any, "
                "so anime is never found.",
                steps=["If you use Prowlarr, make sure the Sonarr app's sync categories include TV/Anime (5070), then "
                       "sync again.",
                       "Otherwise, edit each indexer in Sonarr and add 5070 to Anime Categories."],
                items=[i["name"] for i in active], link=TRASH_SONARR_ANIME))

    if anime_typed and seerr:
        for server in seerr["servers"]:
            s = server["settings"]
            if server["kind"] == "sonarr" and not (s.get("activeAnimeDirectory") and s.get("activeAnimeProfileId")):
                findings.append(finding(
                    f"seerr|{s.get('id')}", AREA, WARNING, "Seerr",
                    f"Seerr sends anime requests for \"{s.get('name')}\" to your normal TV setup",
                    "Seerr's Sonarr settings have no anime root folder or quality profile, so requested anime is added "
                    "like any other show, in the TV folder with the TV profile.",
                    steps=[f"In Seerr, go to Settings > Services, edit \"{s.get('name')}\" and fill in Anime Root "
                           "Folder, Anime Quality Profile and, if you use them, Anime Tags."],
                    link=TRASH_SONARR_ANIME))
    return findings


def check_folders(series, roots):
    roots = [r["path"].rstrip("/") for r in roots]
    by_root = {}
    for s in series:
        by_root.setdefault(root_of(s, roots), []).append(s)
    anime_roots = {r for r in by_root if "anime" in r.lower()}
    if not anime_roots:
        anime_roots = {r for r, ss in by_root.items()
                       if len(ss) >= 3 and sum(s.get("seriesType") == "anime" for s in ss) > len(ss) / 2}
    if not anime_roots:
        return []
    findings = []
    outside = [s for r, ss in by_root.items() if r not in anime_roots for s in ss if s.get("seriesType") == "anime"]
    inside = [s for r in anime_roots for s in by_root[r] if s.get("seriesType") != "anime" and kind(s) is None]
    folders = ", ".join(sorted(anime_roots))
    if outside:
        findings.append(finding(
            "folder-outside", AREA, WARNING, "Sonarr",
            f"{plural(len(outside), 'anime series', 'anime series')} outside your anime folder ({folders})",
            "Keeping anime in its own root folder lets Jellyfin or Plex use anime settings for it, and keeps it "
            "apart from your other TV.",
            steps=["In Sonarr, select the series in Series > Edit Mode, choose the anime root folder and let Sonarr move "
                   "the files. Moves within one share are instant."],
            items=[s["title"] for s in outside], link=TRASH_SONARR_ANIME))
    if inside:
        findings.append(finding(
            "folder-inside", AREA, WARNING, "Sonarr",
            f"{plural(len(inside), 'series', 'series')} in your anime folder that aren't anime",
            "These are in the anime root folder but aren't anime, so they get anime settings in Jellyfin or Plex.",
            steps=["In Sonarr, select the series in Series > Edit Mode and move them to your TV root folder."],
            items=[s["title"] for s in inside], link=TRASH_SONARR_ANIME))
    return findings


def check_profiles(series, profiles):
    anime_profiles = {p["id"] for p in profiles if "anime" in p["name"].lower()}
    if not anime_profiles:
        return []
    names = {p["id"]: p["name"] for p in profiles}
    findings = []
    wrong = [s for s in series if s.get("seriesType") == "anime" and s.get("qualityProfileId") not in anime_profiles]
    if wrong:
        findings.append(finding(
            "profile-wrong", AREA, WARNING, "Sonarr",
            f"{plural(len(wrong), 'anime series', 'anime series')} not using an anime quality profile",
            "Anime releases come from different groups and in different forms (dual audio, BD remuxes, v2 releases), "
            "and a normal TV profile grabs and upgrades them badly.",
            steps=["In Sonarr, select the series in Series > Edit Mode and set the quality profile to your anime one."],
            items=[f"{s['title']} ({names.get(s.get('qualityProfileId'), '?')})" for s in wrong], link=TRASH_SONARR_ANIME))
    misused = [s for s in series if s.get("qualityProfileId") in anime_profiles and s.get("seriesType") != "anime"
               and kind(s) is None]
    if misused:
        findings.append(finding(
            "profile-misused", AREA, WARNING, "Sonarr",
            f"{plural(len(misused), 'series', 'series')} that aren't anime use an anime quality profile",
            "The anime profile prefers anime release groups, so normal shows on it get poor or no releases.",
            steps=["In Sonarr, select the series in Series > Edit Mode and set a TV quality profile."],
            items=[s["title"] for s in misused], link=TRASH_SONARR_ANIME))
    return findings
