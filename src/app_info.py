"""What makes Setuparr Setuparr: its name, port and the apps it connects to."""

NAME = "Setuparr"
SLUG = "setuparr"
VERSION = "0.7.0"
PORT = 9893
TAGLINE = "Checks your Unraid media setup against the TRaSH guides and Unraid's own advice, then fixes what doesn't match with one button, or shows you how to fix it yourself."

# (key, name, default port, where to find the API key)
SERVICES = [
    ("sonarr", "Sonarr", 8989, 'Settings > General > Security in Sonarr'),
    ("radarr", "Radarr", 7878, 'Settings > General > Security in Radarr'),
    ("prowlarr", "Prowlarr", 9696, 'Settings > General > Security in Prowlarr'),
    ("seerr", "Seerr", 5055, 'Settings > General > API Key in Jellyseerr or Overseerr'),
    ("bazarr", "Bazarr", 6767, 'Settings > General > Security > API Key in Bazarr'),
    ("jellyfin", "Jellyfin", 8096, 'Dashboard > API Keys in Jellyfin'),
    ("plex", "Plex", 32400, 'Your Plex token. Plex\'s support article "Finding an authentication token" shows where to find it.'),
    ("qbittorrent", "qBittorrent", 8080, "The password for qBittorrent's web UI. Leave it empty if qBittorrent lets Setuparr in "
                                        "without one."),
    ("transmission", "Transmission", 9091, "The password for Transmission's web UI. Leave it empty if Transmission doesn't ask "
                                           "for one."),
    ("deluge", "Deluge", 8112, "The password for Deluge's web UI (Deluge's own default is deluge)."),
]

# Download clients log in with a password rather than an API key. True: they want a username too.
LOGINS = {"qbittorrent": True, "transmission": True, "deluge": False}


def secret_key(service):
    """The setting that holds an app's API key, or a download client's password."""
    return f"{service}_password" if service in LOGINS else f"{service}_api_key"

# Shown on the Dashboard until each check is ready
PLANNED = [
    {"title": "TRaSH guides", "items": [
        "Custom formats, quality profiles, quality sizes and naming in Sonarr and Radarr.",
        "Prowlarr's sync to your apps, Jellyfin and Plex settings.",
        "SABnzbd and NZBGet folders and categories.",
        "Each difference with a Fix it button that shows the change before making it, or step-by-step instructions.",
    ]},
    {"title": "Genres", "items": [
        "Your own rules, such as Kids, Documentaries or Stand-up going to their own folder, profile and tag, "
        "and a list of everything that doesn't follow them.",
    ]},
    {"title": "More fixes", "items": [
        "Fix it buttons for merging duplicate tags, FlareSolverr tags and Seerr's tags, and for download client settings.",
        "Anime checks for Radarr.",
    ]},
]


def extra_fields(F, env_str, env_bool, env_int):
    return [
        F("check_interval_hours", "general", "Run the checks every (hours)", "int",
          env_int("CHECK_INTERVAL_HOURS", default=24), section="Checks", min=1, max=720,
          help="Setuparr only reads from your apps when it checks. Fixes are only ever made when you press Fix it."),
    ]
