"""What makes Setuparr Setuparr: its name, port and the apps it connects to."""

NAME = "Setuparr"
SLUG = "setuparr"
VERSION = "0.3.0"
PORT = 9893
TAGLINE = "Checks your Unraid media setup against the TRaSH guides, then fixes what doesn't match with one button, or shows you how to fix it yourself."

# (key, name, default port, where to find the API key)
SERVICES = [
    ("sonarr", "Sonarr", 8989, 'Settings > General > Security in Sonarr'),
    ("radarr", "Radarr", 7878, 'Settings > General > Security in Radarr'),
    ("prowlarr", "Prowlarr", 9696, 'Settings > General > Security in Prowlarr'),
    ("seerr", "Seerr", 5055, 'Settings > General > API Key in Jellyseerr or Overseerr'),
    ("jellyfin", "Jellyfin", 8096, 'Dashboard > API Keys in Jellyfin'),
    ("plex", "Plex", 32400, 'Your Plex token. Plex\'s support article "Finding an authentication token" shows where to find it.'),
]

# Shown on the Dashboard until each check is ready
PLANNED = [
    {"title": "TRaSH guides", "items": [
        "Custom formats, quality profiles, quality sizes and naming in Sonarr and Radarr.",
        "Prowlarr's sync to your apps, Jellyfin and Plex settings.",
        "Each difference with a Fix it button that shows the change before making it, or step-by-step instructions.",
    ]},
    {"title": "Genres", "items": [
        "Your own rules, such as Kids, Documentaries or Stand-up going to their own folder, profile and tag, "
        "and a list of everything that doesn't follow them.",
    ]},
    {"title": "More fixes", "items": [
        "Fix it buttons for merging duplicate tags, FlareSolverr tags and Seerr's tags.",
        "Anime checks for Radarr.",
    ]},
]


def extra_fields(F, env_str, env_bool, env_int):
    return [
        F("check_interval_hours", "general", "Run the checks every (hours)", "int",
          env_int("CHECK_INTERVAL_HOURS", default=24), section="Checks", min=1, max=720,
          help="Setuparr only reads from your apps when it checks. Fixes are only ever made when you press Fix it."),
    ]
