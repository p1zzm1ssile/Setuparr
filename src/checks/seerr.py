"""Seerr's (Overseerr or Jellyseerr) links to Sonarr and Radarr: where requests are sent and how they're added."""

from checks import PROBLEM, TIP, WARNING, finding

AREA = "seerr"
SEERR_DOCS = "https://docs.overseerr.dev/using-overseerr/settings#services"
APPS = {"sonarr": "Sonarr", "radarr": "Radarr"}


def check(d):
    """d holds servers: [{kind, settings, live}], where live is what Seerr reads from that server now (its profiles,
    root folders and tags), or None if it couldn't."""
    findings = check_defaults(d["servers"])
    for server in d["servers"]:
        findings += check_server(server)
    return findings


def check_defaults(servers):
    findings = []
    for kind, app in APPS.items():
        for is4k in (False, True):
            group = [s["settings"] for s in servers if s["kind"] == kind and bool(s["settings"].get("is4k")) == is4k]
            if group and not any(s.get("isDefault") for s in group):
                what = f"4K {'TV' if kind == 'sonarr' else 'film'}" if is4k else ("TV" if kind == "sonarr" else "film")
                findings.append(finding(
                    f"no-default|{kind}|{is4k}", AREA, PROBLEM, "Seerr", f"Seerr has no default {app} for {what} requests",
                    f"None of Seerr's {'4K ' if is4k else ''}{app} servers is marked as the default, so approved "
                    f"{what} requests aren't sent anywhere.",
                    steps=[f"In Seerr, go to Settings > Services, edit the {app} server requests should go to and tick "
                           f"Default Server{' (and 4K Server)' if is4k else ''}."],
                    items=[s.get("name") or "?" for s in group], link=SEERR_DOCS))
    return findings


def check_server(server):
    s, kind, live = server["settings"], server["kind"], server.get("live")
    app, name = APPS[kind], server["settings"].get("name") or "?"
    if live is None:
        return [finding(
            f"unreachable|{kind}|{s.get('id')}", AREA, WARNING, "Seerr", f"Seerr can't reach {app} \"{name}\"",
            f"Seerr couldn't read the profiles and folders from \"{name}\", so requests sent to it fail until it can.",
            steps=[f"In Seerr, go to Settings > Services, edit \"{name}\" and press Test. Check the address, port and "
                   f"API key match {app}'s, and that {app} is running."],
            link=SEERR_DOCS)]
    findings = []
    profiles = {p.get("id"): p.get("name") for p in live.get("profiles") or []}
    folders = {str(f.get("path") or "").rstrip("/") for f in live.get("rootFolders") or []}
    for field, folder_field, what in (("activeProfileId", "activeDirectory", "requests"),
                                      ("activeAnimeProfileId", "activeAnimeDirectory", "anime requests")):
        profile, folder = s.get(field), str(s.get(folder_field) or "").rstrip("/")
        if profile and profiles and profile not in profiles:
            findings.append(finding(
                f"profile|{kind}|{s.get('id')}|{field}", AREA, PROBLEM, "Seerr",
                f"Seerr sends {what} to \"{name}\" with a quality profile that no longer exists",
                f"Seerr remembers the profile by number, and {app} has no profile #{profile} any more"
                + (f" (it was {s.get('activeProfileName')})" if field == "activeProfileId" and s.get("activeProfileName") else "")
                + f". {app} turns these requests down, so nothing is added.",
                steps=[f"In Seerr, go to Settings > Services, edit \"{name}\", choose the quality profile again and save."],
                items=sorted(str(n) for n in profiles.values()), link=SEERR_DOCS))
        if folder and folders and folder not in folders:
            findings.append(finding(
                f"folder|{kind}|{s.get('id')}|{folder_field}", AREA, PROBLEM, "Seerr",
                f"Seerr sends {what} to \"{name}\" with a root folder {app} doesn't have",
                f"{folder} isn't one of {app}'s root folders any more, so {app} turns these requests down.",
                steps=[f"In Seerr, go to Settings > Services, edit \"{name}\", choose the root folder again and save."],
                items=sorted(folders), link=SEERR_DOCS))
    if kind == "radarr" and s.get("minimumAvailability") == "announced":
        findings.append(finding(
            f"announced|{s.get('id')}", AREA, TIP, "Seerr",
            "Films requested through Seerr can be grabbed as soon as they're announced",
            f"\"{name}\" adds requested films with Minimum Availability set to Announced, so Radarr searches before "
            "they're out, when the only releases are cams and fakes.",
            steps=[f"In Seerr, go to Settings > Services, edit \"{name}\" and set Minimum Availability to Released."],
            link=SEERR_DOCS))
    return findings
