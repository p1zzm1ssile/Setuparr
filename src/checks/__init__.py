"""Setuparr's checks. Each one returns a list of findings made with finding()."""

import hashlib

PROBLEM = "problem"  # something is broken or will break
WARNING = "warning"  # works, but not as well as it should
TIP = "tip"          # tidy-up, or worth knowing

SEVERITY_ORDER = {PROBLEM: 0, WARNING: 1, TIP: 2}

TRASH_UNRAID = "https://trash-guides.info/File-and-Folder-Structure/How-to-set-up/Unraid/"
TRASH_HARDLINKS = "https://trash-guides.info/File-and-Folder-Structure/Hardlinks-and-Instant-Moves/"
TRASH_SONARR_ANIME = "https://trash-guides.info/Sonarr/Sonarr-setup-quality-profiles-anime/"
TRASH_SONARR_NAMING = "https://trash-guides.info/Sonarr/Sonarr-recommended-naming-scheme/"


def finding(key, area, severity, app, title, detail, steps=(), items=(), link=None, fix=None):
    """One thing Setuparr found.

    key identifies the finding across runs (so a fix can find it again), and
    fix, when given, is {"action", "params", "label", "confirm"}: only ever
    carried out by the server from its own stored copy, never from the browser.
    """
    return {
        "id": hashlib.sha1(f"{area}|{app}|{key}".encode()).hexdigest()[:12],
        "area": area,
        "severity": severity,
        "app": app,
        "title": title,
        "detail": detail,
        "steps": list(steps),
        "items": sorted(items)[:200] if items else [],
        "item_count": len(items),
        "link": link,
        "fix": fix,
    }


def names(things, key="title"):
    return [t.get(key) or "?" for t in things]


def plural(n, word, many=None):
    return f"{n} {word if n == 1 else (many or word + 's')}"
