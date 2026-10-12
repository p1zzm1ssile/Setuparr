"""Tags in Sonarr, Radarr, Prowlarr and Seerr.

In Sonarr and Radarr, an indexer, download client, delay profile or release
profile with a tag only applies to series or films with that tag. So a tag
nothing has switches those off, and tagging every indexer leaves untagged
items with nothing to search.
"""

import re

from checks import PROBLEM, TIP, WARNING, finding, plural

AREA = "tags"
ITEMS = {"sonarr": ("seriesIds", "series", "series"), "radarr": ("movieIds", "film", "films")}
THINGS = {
    "indexerIds": "indexer", "downloadClientIds": "download client", "delayProfileIds": "delay profile",
    "releaseProfileIds": "release profile", "restrictionIds": "release profile", "importListIds": "import list",
    "notificationIds": "connection", "autoTagIds": "auto-tagging rule",
    "indexerProxyIds": "indexer proxy", "applicationIds": "app",
}
# Things that do nothing for an item unless it has the tag
LIMITING = ("indexerIds", "downloadClientIds", "delayProfileIds", "releaseProfileIds", "restrictionIds")


def linked(tag):
    return {k: v for k, v in tag.items() if k.endswith("Ids") and v}


def delete_fix(service, label, tag):
    return {"action": "delete_tag", "params": {"service": service, "tag_id": tag["id"], "label": tag["label"]},
            "label": "Delete tag", "confirm": f"Delete the tag \"{tag['label']}\" from {label}? Nothing uses it. "
                                              "Setuparr keeps a copy so you can undo this."}


def similar_tags(service, label, tags):
    groups = {}
    for t in tags:
        key = re.sub(r"[^a-z0-9]", "", t["label"].lower())
        key = key[:-1] if key.endswith("s") and len(key) > 3 else key
        groups.setdefault(key, []).append(t["label"])
    return [finding(
        f"similar|{key}", AREA, TIP, label,
        f"{label} has tags that look like the same thing",
        "Two tags with nearly the same name usually mean one was added by mistake, and things tagged with one "
        "don't match things tagged with the other.",
        steps=[f"In {label}, give everything one of these tags (Settings > Tags shows what uses each), "
               "then delete the other."],
        items=labels) for key, labels in groups.items() if len(labels) > 1]


def check_arr(service, label, d):
    """Sonarr or Radarr. d holds tags (tag/detail), indexers, clients and items."""
    items_key, one, many = ITEMS[service]
    findings = []
    names = {"indexerIds": {i["id"]: i["name"] for i in d["indexers"]},
             "downloadClientIds": {c["id"]: c["name"] for c in d["clients"]}}

    for tag in d["tags"]:
        links = linked(tag)
        if not links:
            findings.append(finding(
                f"unused|{tag['id']}", AREA, TIP, label, f"The tag \"{tag['label']}\" isn't used in {label}",
                f"Nothing in {label} has this tag: no {many}, indexers, download clients, profiles or connections.",
                steps=[f"Delete it in {label} under Settings > Tags, or press Delete tag."],
                fix=delete_fix(service, label, tag)))
            continue
        limited = {k: v for k, v in links.items() if k in LIMITING}
        if limited and not tag.get(items_key) and not tag.get("autoTagIds"):
            what = [f"{THINGS[k]}: {names.get(k, {}).get(i, f'#{i}')}" for k, ids in limited.items() for i in ids]
            findings.append(finding(
                f"unmatched|{tag['id']}", AREA, WARNING, label,
                f"Nothing has the tag \"{tag['label']}\", so what's limited to it is never used",
                f"In {label}, an indexer, download client or profile with a tag only applies to {many} with that tag. "
                f"No {one} has \"{tag['label']}\", so these never do anything.",
                steps=[f"Add the tag to the {many} that should use them (or set up an auto-tagging rule), "
                       "or remove the tag from them so they apply to everything."],
                items=what))

    untagged = [i for i in d["items"] if not i.get("tags")]
    for things, enabled, word in (
        (d["indexers"], lambda i: i.get("enableRss") or i.get("enableAutomaticSearch"), "indexers"),
        (d["clients"], lambda c: c.get("enable"), "download clients"),
    ):
        for protocol in ("torrent", "usenet"):
            active = [t for t in things if enabled(t) and t.get("protocol") == protocol]
            if untagged and active and all(t.get("tags") for t in active):
                findings.append(finding(
                    f"all-tagged|{word}|{protocol}", AREA, PROBLEM, label,
                    f"Every {protocol} {word[:-1]} in {label} has a tag, so untagged {many} have none",
                    f"In {label}, {word} with a tag are only used for {many} with that tag. All your {protocol} {word} "
                    f"have tags, so {plural(len(untagged), one, many)} with no tags can't use any of them.",
                    steps=[f"Remove the tags from at least one {protocol} {word[:-1]} so it's used for everything, "
                           f"or tag the {many} below."],
                    items=[i.get("title", "?") for i in untagged]))

    findings += similar_tags(service, label, d["tags"])
    return findings


def check_prowlarr(d):
    """d holds tags (tag/detail), indexers, proxies and apps."""
    label = "Prowlarr"
    findings = []
    indexer_tags = {t for i in d["indexers"] if i.get("enable") for t in i.get("tags") or []}
    tag_names = {t["id"]: t["label"] for t in d["tags"]}

    for tag in d["tags"]:
        if not linked(tag):
            findings.append(finding(
                f"unused|{tag['id']}", AREA, TIP, label, f"The tag \"{tag['label']}\" isn't used in Prowlarr",
                "No indexer, proxy, app or notification has this tag.",
                steps=["Delete it in Prowlarr under Settings > Tags, or press Delete tag."],
                fix=delete_fix("prowlarr", label, tag)))

    for proxy in d["proxies"]:
        if proxy.get("implementation", "").lower() != "flaresolverr":
            continue
        if not proxy.get("tags"):
            findings.append(finding(
                f"proxy-untagged|{proxy['id']}", AREA, WARNING, label,
                f"The FlareSolverr proxy \"{proxy['name']}\" has no tag, so no indexer uses it",
                "Prowlarr only sends an indexer through FlareSolverr when they share a tag. Without one, indexers "
                "behind Cloudflare keep failing.",
                steps=["In Prowlarr, go to Settings > Indexers, edit FlareSolverr and give it a tag such as flaresolverr.",
                       "Add the same tag to each indexer that's blocked by Cloudflare."]))
        elif not set(proxy["tags"]) & indexer_tags:
            findings.append(finding(
                f"proxy-unused|{proxy['id']}", AREA, WARNING, label,
                f"No indexer shares a tag with the FlareSolverr proxy \"{proxy['name']}\"",
                "Prowlarr only sends an indexer through FlareSolverr when they share a tag, and no enabled indexer "
                "has one of its tags.",
                steps=["Add the proxy's tag to each indexer that's blocked by Cloudflare."],
                items=[tag_names.get(t, f"#{t}") for t in proxy["tags"]]))

    for app in d["apps"]:
        if app.get("syncLevel") == "disabled" or not app.get("tags"):
            continue
        if not set(app["tags"]) & indexer_tags:
            findings.append(finding(
                f"app-unmatched|{app['id']}", AREA, PROBLEM, label,
                f"Prowlarr sends no indexers to {app['name']}",
                f"{app['name']} has a tag in Prowlarr, so only indexers with that tag are sent to it, and no enabled "
                "indexer has it.",
                steps=[f"Add the tag to the indexers {app['name']} should use, or remove the tag from {app['name']} "
                       "in Settings > Apps so it gets every indexer."],
                items=[tag_names.get(t, f"#{t}") for t in app["tags"]]))
    findings += similar_tags("prowlarr", label, d["tags"])
    return findings


def check_seerr(d):
    """d holds servers: [{kind, settings, live_tags}]."""
    findings = []
    for server in d["servers"]:
        s, kind = server["settings"], server["kind"]
        live = server.get("live_tags")
        if live is None:
            continue
        live_ids = {t["id"] for t in live}
        app = "Sonarr" if kind == "sonarr" else "Radarr"
        for field, what in (("tags", "requests"), ("animeTags", "anime requests")):
            missing = [t for t in s.get(field) or [] if t not in live_ids]
            if missing:
                findings.append(finding(
                    f"missing|{kind}|{s.get('id')}|{field}", AREA, PROBLEM, "Seerr",
                    f"Seerr tags {what} for \"{s.get('name')}\" with tags that no longer exist in {app}",
                    f"Seerr remembers {app}'s tags by number. These were deleted in {app}, so requests either fail to "
                    f"add or arrive without the tag, and anything limited to it doesn't apply.",
                    steps=[f"In Seerr, go to Settings > Services, edit \"{s.get('name')}\" and choose the tags again."],
                    items=[f"Tag #{t}" for t in missing]))
    return findings
