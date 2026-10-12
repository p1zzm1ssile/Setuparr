"""What makes Setuparr Setuparr: its name, port and the apps it connects to."""

NAME = "Setuparr"
SLUG = "setuparr"
VERSION = "0.1.0"
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
    {"title": 'Your Unraid setup', "items": [
        "Reads only the files you upload: the Docker templates and share settings from your flash drive. It never scans your server or needs the Docker socket. Passwords and keys in them are blanked as soon as they're opened.",
        'Hardlinks and instant moves: whether your download client and Sonarr/Radarr share one /data folder, as the TRaSH guide sets out.',
        "Folders that don't match between your download client and Sonarr/Radarr, so downloads never import.",
        '/mnt/user mixed with /mnt/cache or /mnt/diskX for the same files.',
        'PUID, PGID and UMASK that differ between containers.',
        'Share cache and mover settings for your data and appdata shares.',
    ]},
    {"title": 'Tags', "items": [
        'Indexers and download clients that only work for tagged items, leaving untagged ones with nowhere to search or download.',
        'Tags on indexers, download clients, delay and release profiles that nothing uses.',
        'Duplicate tags like 4k and 4K, and tags left over from old setups.',
        "Prowlarr indexers that miss the FlareSolverr tag, or don't reach an app because the tags don't match.",
        'Tags Seerr adds to requests that no longer exist in Sonarr or Radarr.',
    ]},
    {"title": 'Anime', "items": [
        'Anime series set to Standard instead of Anime in Sonarr, which searches with the wrong episode numbers.',
        'Anime outside your anime folder, quality profile or tag, and other shows inside them.',
        'Anime naming and quality profiles against the TRaSH anime guide.',
        "Indexers missing the anime category, and Seerr's anime settings.",
    ]},
    {"title": 'TRaSH guides', "items": [
        'Custom formats, quality profiles, quality sizes and naming in Sonarr and Radarr.',
        "Prowlarr's sync to your apps, Jellyfin and Plex settings.",
        "Each difference has a Fix it button that shows the change before making it and keeps a backup, or step-by-step instructions for the things it shouldn't change itself.",
    ]},
    {"title": 'Genres', "items": [
        "Your own rules, such as Kids, Documentaries or Stand-up going to their own folder, profile and tag, and a list of everything that doesn't follow them.",
    ]},
]


def extra_fields(F, env_str, env_bool, env_int):
    return []
