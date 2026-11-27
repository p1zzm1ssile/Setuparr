"""The limits set with the sliders on the Sonarr and Radarr tabs: the highest resolution and the largest file wanted.

Each check compares one thing with a limit: the qualities the profiles in use allow, the quality sizes (MB per
minute, so worked out from how long the films or episodes run), and what's already in the library.
"""

from checks import TIP, WARNING, finding, plural

AREA = "limits"
LINKS = {
    "sonarr": {"profiles": "https://trash-guides.info/Sonarr/sonarr-setup-quality-profiles/",
               "sizes": "https://trash-guides.info/Sonarr/Sonarr-Quality-Settings-File-Size/"},
    "radarr": {"profiles": "https://trash-guides.info/Radarr/radarr-setup-quality-profiles/",
               "sizes": "https://trash-guides.info/Radarr/Radarr-Quality-Settings-File-Size/"},
}
# (one, many, what one runtime covers), and the runtime in minutes used when the library has none to go by
THINGS = {"radarr": ("film", "films", "film", 120), "sonarr": ("series", "series", "episode", 45)}
MB_PER_GB = 1024  # Sonarr and Radarr count sizes in MiB


def check(service, label, d, max_resolution=0, max_size_gb=0):
    """d holds what fetch_arr reads. A limit of 0 is no limit."""
    if service not in THINGS:
        return []
    return (check_profiles(service, label, d, max_resolution)
            + check_sizes(service, label, d.get("definitions"), mb_per_minute(service, d, max_size_gb))
            + check_library(service, label, d.get("items"), max_resolution, max_size_gb))


def resolution_name(resolution):
    return "4K" if resolution == 2160 else f"{resolution}p"


# ---------- Resolution ----------


def _qualities(item):
    """The qualities in a profile item: the item itself, or every quality in a group."""
    if item.get("items"):
        return [i.get("quality") or {} for i in item["items"]]
    return [item.get("quality") or {}]


def too_high(item, max_resolution):
    """Whether every quality in a profile item is above the limit. A group that's partly above it is left, since
    Sonarr and Radarr turn a group on or off as a whole."""
    qualities = _qualities(item)
    return bool(qualities) and all((q.get("resolution") or 0) > max_resolution for q in qualities)


def profiles_in_use(d):
    """The profiles anything in the library uses, or every profile while the library is empty."""
    used = {i.get("qualityProfileId") for i in d.get("items") or []}
    profiles = d.get("profiles") or []
    return [p for p in profiles if p.get("id") in used] if used else profiles


def capped_profile(profile, max_resolution):
    """A copy of the profile with the qualities above the limit turned off and Upgrade Until moved down to the
    highest one left, or None when nothing would be left."""
    capped = {**profile, "items": [dict(i) for i in profile.get("items") or []]}
    for item in capped["items"]:
        if item.get("allowed") and too_high(item, max_resolution):
            item["allowed"] = False
            if item.get("items"):
                item["items"] = [{**i, "allowed": False} for i in item["items"]]
    allowed = [i for i in capped["items"] if i.get("allowed")]
    if not allowed:
        return None
    cutoff = next((i for i in capped["items"]
                   if (i.get("id") if i.get("items") else (i.get("quality") or {}).get("id")) == profile.get("cutoff")),
                  None)
    if cutoff is None or not cutoff.get("allowed"):
        # Items go from lowest to highest, so the last one still allowed is the best one left
        best = allowed[-1]
        capped["cutoff"] = best.get("id") if best.get("items") else (best.get("quality") or {}).get("id")
    return capped


def check_profiles(service, label, d, max_resolution):
    if not max_resolution:
        return []
    over, lines = [], []
    for p in profiles_in_use(d):
        names = [i.get("name") or (i.get("quality") or {}).get("name") or "?"
                 for i in p.get("items") or [] if i.get("allowed") and too_high(i, max_resolution)]
        if names:
            over.append(p)
            lines.append(f"{p.get('name')}: {', '.join(reversed(names))}")
    if not over:
        return []
    one, many, *_ = THINGS[service]
    fixable = [p for p in over if capped_profile(p, max_resolution)]
    stuck = [p.get("name") for p in over if p not in fixable]
    limit = resolution_name(max_resolution)
    return [finding(
        "resolution", AREA, WARNING, label,
        f"{plural(len(over), 'profile')} in {label} can grab {many} above {limit}",
        f"You set {limit} as the highest resolution you want for {many}, but "
        f"{'this profile allows' if len(over) == 1 else 'these profiles allow'} qualities above it, so {label} will "
        f"grab or upgrade to them, at several times the size."
        + (f" {', '.join(stuck)} only {'allows' if len(stuck) == 1 else 'allow'} qualities above {limit}, so "
           f"{'it needs' if len(stuck) == 1 else 'they need'} changing by hand, or the {many} using "
           f"{'it' if len(stuck) == 1 else 'them'} moving to another profile." if stuck else ""),
        steps=([f"Press Turn off above {limit} to untick those qualities. If Upgrade Until is one of them it moves "
                "down to the best quality left. It can be undone under Recent fixes."] if fixable else [])
        + [f"Or in {label}, go to Settings > Profiles, open each profile below and untick the qualities listed.",
           f"Or move the slider on {label}'s tab in Setuparr's Settings, if you do want them."],
        items=lines, link=LINKS[service]["profiles"],
        fix={"action": "cap_resolution",
             "params": {"service": service, "max_resolution": max_resolution,
                        "profile_ids": [p.get("id") for p in fixable]},
             "label": f"Turn off above {limit}",
             "confirm": f"Turn off the qualities above {limit} in {plural(len(fixable), 'profile')} in {label}? You "
                        "can undo it under Recent fixes."} if fixable else None)]


# ---------- Size ----------


def typical_runtime(service, items):
    """Minutes that most films, or most series' episodes, fit in: the 90th percentile of the library's runtimes, so
    the limit holds for nearly everything and only the longest go over."""
    runtimes = sorted(i["runtime"] for i in items or [] if isinstance(i.get("runtime"), (int, float)) and i["runtime"] > 0)
    if not runtimes:
        return THINGS[service][3]
    return runtimes[min(len(runtimes) - 1, int(len(runtimes) * 0.9))]


def mb_per_minute(service, d, max_size_gb):
    """The largest quality size (MB per minute) that keeps to the size limit, or None for no limit."""
    if not max_size_gb or service not in THINGS:
        return None
    return round(max_size_gb * MB_PER_GB / typical_runtime(service, d.get("items")), 1)


def over_limit(definitions, cap):
    """The quality definitions whose largest size is unlimited or above cap (MB per minute)."""
    return [q for q in definitions or [] if q.get("maxSize") is None or float(q["maxSize"]) > cap + 0.05]


def capped_definitions(definitions, cap):
    """A copy of the quality definitions with every size above cap brought down to it."""
    out = []
    for q in definitions or []:
        new = dict(q)
        for field in ("minSize", "preferredSize", "maxSize"):
            # An empty largest or preferred size is unlimited
            if field != "minSize" and new.get(field) is None:
                new[field] = cap
            elif new.get(field) is not None and float(new[field]) > cap:
                new[field] = cap
        out.append(new)
    return out


def check_sizes(service, label, definitions, cap):
    if not cap or not definitions:
        return []
    over = over_limit(definitions, cap)
    if not over:
        return []
    one, many, per, _ = THINGS[service]
    lines = [f"{(q.get('quality') or {}).get('name') or q.get('title') or '?'}: "
             + ("unlimited" if q.get("maxSize") is None else f"{float(q['maxSize']):g} MB per minute") for q in over]
    return [finding(
        "sizes", AREA, WARNING, label, f"{label} can grab {many} bigger than you want",
        f"{label} limits sizes in MB per minute of runtime. To keep each {per} under your limit, nearly all of yours "
        f"would need {cap:g} MB per minute or less, but {plural(len(over), 'quality allows', 'qualities allow')} more.",
        steps=[f"Press Keep to my limit to bring those sizes down to {cap:g} MB per minute. It can be undone under "
               "Recent fixes.",
               f"Or set them by hand in {label} under Settings > Quality (Show Advanced)."],
        items=lines, link=LINKS[service]["sizes"],
        fix={"action": "cap_quality_sizes", "params": {"service": service, "cap": cap},
             "label": "Keep to my limit",
             "confirm": f"Set the largest size of {plural(len(over), 'quality', 'qualities')} in {label} to {cap:g} MB "
                        "per minute? You can undo it under Recent fixes."})]


# ---------- What's already there ----------


def _gb(size):
    return f"{size / MB_PER_GB ** 3:.1f} GB"


def check_library(service, label, items, max_resolution, max_size_gb):
    if not max_resolution and not max_size_gb:
        return []
    one, many, per, _ = THINGS[service]
    limit_bytes = max_size_gb * MB_PER_GB ** 3
    big, high = [], []
    for item in items or []:
        name = item.get("title") or "?"
        if service == "radarr":
            file = item.get("movieFile") or {}
            size = file.get("size") or item.get("sizeOnDisk") or 0
            resolution = ((file.get("quality") or {}).get("quality") or {}).get("resolution") or 0
            if max_resolution and resolution > max_resolution:
                high.append(f"{name} ({resolution_name(resolution)})")
            if max_size_gb and size > limit_bytes:
                big.append(f"{name} ({_gb(size)})")
        else:
            stats = item.get("statistics") or {}
            count, size = stats.get("episodeFileCount") or 0, stats.get("sizeOnDisk") or 0
            if max_size_gb and count and size / count > limit_bytes:
                big.append(f"{name} ({_gb(size / count)} per episode)")
    findings = []
    leave = (f"{label} won't swap a file for a lower quality on its own. To get a smaller copy, delete the file in "
             f"{label} and search again once the profile and sizes keep to your limits.")
    if high:
        findings.append(finding(
            "library-resolution", AREA, TIP, label,
            f"{plural(len(high), one, many)} in {label} {'is' if len(high) == 1 else 'are'} above "
            f"{resolution_name(max_resolution)}",
            f"These were grabbed above the highest resolution you set. Nothing needs doing if you're happy to keep "
            f"them.", steps=[leave], items=high))
    if big:
        limit = f"{max_size_gb:g} GB" + (" an episode" if service == "sonarr" else "")
        findings.append(finding(
            "library-size", AREA, TIP, label,
            f"{plural(len(big), one, many)} in {label} {'is' if len(big) == 1 else 'are'} over {limit}",
            "These are bigger than the largest size you set"
            + (", on average across their episodes" if service == "sonarr" else "")
            + ". Nothing needs doing if you're happy to keep them.", steps=[leave], items=big))
    return findings
