"""Prowlarr: its sync to Sonarr, Radarr and Lidarr, indexers it has paused after errors, and its own health,
login and backups. Its tags are checked in tags.py."""

from datetime import datetime, timezone

from checks import PROBLEM, TIP, WARNING, arr, finding, plural

AREA = "prowlarr"
LABEL = "Prowlarr"
WIKI_APPS = "https://wiki.servarr.com/prowlarr/settings#applications"
WIKI_INDEXERS = "https://wiki.servarr.com/prowlarr/indexers"
LOCAL = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")
# Health messages this module explains itself
COVERED_HEALTH = {"IndexerStatusCheck", "IndexerLongTermStatusCheck"}


def check(d, snapshot=None, connected=None):
    """d holds what fetch_prowlarr reads. connected: {key: label} of the apps Setuparr itself is connected to."""
    findings = (check_apps(d.get("apps") or [], connected or {}, snapshot) + check_indexers(d)
                + arr.check_health(LABEL, [h for h in d.get("health") or [] if h.get("source") not in COVERED_HEALTH])
                + arr.check_security(LABEL, d.get("host")) + arr.check_backups(LABEL, d.get("host"), None))
    for f in findings:
        f["area"] = AREA
    return findings


def field(app, name):
    return next((f.get("value") for f in app.get("fields") or [] if f.get("name") == name), None)


def host_network(snapshot):
    """Whether Prowlarr's container uses the host's network, where localhost is Unraid itself."""
    for c in (snapshot or {}).get("containers", []):
        if "prowlarr" in f"{c['image']} {c['name']}".lower():
            return c.get("network") == "host"
    return False


def check_apps(apps, connected, snapshot):
    findings = []
    linked = {str(a.get("implementation") or "").lower() for a in apps}
    for key, label in connected.items():
        if key not in linked:
            findings.append(finding(
                f"app-missing|{key}", AREA, WARNING, LABEL, f"Prowlarr doesn't send indexers to {label}",
                f"You use {label}, but it isn't one of Prowlarr's apps, so Prowlarr's indexers aren't added to it and "
                "you have to keep them up to date in two places.",
                steps=[f"In Prowlarr, go to Settings > Apps, press + and choose {label}.",
                       f"Set Prowlarr Server to Prowlarr's address and {label} Server to {label}'s, as seen from "
                       f"Prowlarr (Unraid's IP and the port), paste {label}'s API key and set Sync Level to Full Sync."],
                link=WIKI_APPS))
    for a in apps:
        name, level = a.get("name") or a.get("implementation") or "?", a.get("syncLevel")
        if level == "disabled":
            findings.append(finding(
                f"sync-off|{a.get('id')}", AREA, WARNING, LABEL, f"Prowlarr's sync to {name} is turned off",
                f"Sync Level is Disabled, so new indexers and changes in Prowlarr never reach {name}.",
                steps=[f"In Prowlarr, go to Settings > Apps, edit {name} and set Sync Level to Full Sync."],
                link=WIKI_APPS))
        elif level == "addOnly":
            findings.append(finding(
                f"sync-add-only|{a.get('id')}", AREA, TIP, LABEL, f"Prowlarr only adds indexers to {name}",
                f"Sync Level is Add and Remove Only, so when you change an indexer in Prowlarr (its settings, "
                f"categories or tags) {name} keeps the old version.",
                steps=[f"In Prowlarr, go to Settings > Apps, edit {name} and set Sync Level to Full Sync, unless you "
                       f"change indexers in {name} by hand on purpose."],
                link=WIKI_APPS))
        if level == "disabled" or host_network(snapshot):
            continue
        for setting, what in (("prowlarrUrl", "Prowlarr Server"), ("baseUrl", f"{name} Server")):
            value = str(field(a, setting) or "").lower()
            if any(f"//{l}" in value for l in LOCAL):
                findings.append(finding(
                    f"localhost|{a.get('id')}|{setting}", AREA, PROBLEM, LABEL,
                    f"Prowlarr's {what} for {name} uses localhost",
                    f"{what} is {value}. Inside a container, localhost is that container itself, so "
                    + (f"{name} can't reach Prowlarr to search." if setting == "prowlarrUrl"
                       else f"Prowlarr can't reach {name} to send it indexers."),
                    steps=[f"In Prowlarr, go to Settings > Apps, edit {name} and use Unraid's IP address instead of "
                           "localhost (or the container's name, if both are on the same custom Docker network)."],
                    link=WIKI_APPS))
    return findings


def paused(status, now=None):
    """The ids of indexers Prowlarr has paused after errors."""
    now = now or datetime.now(timezone.utc)
    out = set()
    for s in status or []:
        till = s.get("disabledTill")
        try:
            when = datetime.fromisoformat(str(till).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when > now:
            out.add(s.get("indexerId"))
    return out


def check_indexers(d, now=None):
    indexers = d.get("indexers") or []
    enabled = [i for i in indexers if i.get("enable")]
    if not enabled:
        return [finding(
            "no-indexers", AREA, PROBLEM, LABEL, "Prowlarr has no indexers turned on",
            "Without indexers, Sonarr, Radarr and Lidarr have nowhere to search, so nothing new is ever found.",
            steps=["In Prowlarr, go to Indexers, press Add Indexer and add the trackers or Usenet indexers you use."],
            link=WIKI_INDEXERS)]
    stopped = paused(d.get("status"), now)
    failing = [i for i in enabled if i.get("id") in stopped]
    if not failing:
        return []
    every = len(failing) == len(enabled)
    return [finding(
        "paused", AREA, PROBLEM if every else WARNING, LABEL,
        "Prowlarr has paused all of its indexers" if every
        else f"Prowlarr has paused {plural(len(failing), 'indexer')} after errors",
        "Prowlarr stops using an indexer for a while after it fails, and for longer each time it fails again. Searches "
        "skip it until then" + (", so nothing can be found at the moment." if every else "."),
        steps=["In Prowlarr, open System > Status, or press Test on each indexer, to see why it's failing.",
               "Common causes: the site is down, Cloudflare is blocking it (add a FlareSolverr proxy and tag), your "
               "login or API key has expired, or you've hit the site's limit."],
        items=[i.get("name") or "?" for i in failing], link=WIKI_INDEXERS)]
