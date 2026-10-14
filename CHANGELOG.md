# Changelog

## 0.4.1

- Download client passwords are saved as `qbittorrent_password`, `transmission_password` and `deluge_password` in
  `settings.json`, not under an API key name. Passwords saved by 0.4.0 are moved over when Setuparr starts.
- Findings that come from your Unraid settings now say when those settings were sent, with a link to send them again.
  They don't change after you change a container until you do.
- Apps Setuparr can't read from are written to the log too, not only shown on the Dashboard.

## 0.4.0

- **Download clients:** connect qBittorrent, Transmission and Deluge under Settings. Setuparr only reads their settings,
  and checks where they save downloads, unfinished downloads on another folder mapping, qBittorrent's torrent
  management mode, pre-allocation on a cached share and networks let in without a password.
- **Sonarr and Radarr download client checks:** Completed Download Handling off, clients at `localhost`, clients with no
  category, Sonarr and Radarr sharing a category, and Deluge's Label plugin.
- **Library checks** in Sonarr and Radarr: root folders they can't reach, hardlinks off, Propers and Repacks against the
  TRaSH Repack/Proper custom format, and renaming off. Radarr now gets its root folders, profiles and naming read too.
- **Unraid:** a downloads or media share the mover moves onto the cache.

## 0.3.0

- **Nothing is written to disk any more.** The terminal command on the Unraid page now sends the Unraid files straight
  to Setuparr with a one-time link (it works once, for 15 minutes), instead of leaving a file in appdata.
- A leftover `unraid-config.tar.gz` from 0.2.0 can be read and deleted, or just deleted, from the Unraid page.
- Even less is kept from the Unraid files: ports, networks and time zones are now dropped too, as no check needs them.
  The Unraid page and README say exactly what's kept.

## 0.2.1

- Jellyfin 10.11 no longer turns down the API key: the key is now sent the way newer Jellyfin expects.

## 0.2.0

- **Unraid page:** import your Docker templates, share settings and compose files from Unraid's terminal or by choosing
  them. Only folder mappings, images, networks, ports and PUID/PGID/UMASK/TZ are kept; passwords and keys never are.
- **Unraid checks:** hardlinks and instant moves, downloads a library app can't reach, paths that need a Remote Path
  Mapping, `/mnt/user` mixed with disk or pool paths, PUID/PGID/UMASK, and appdata, system and data shares.
- **Tag checks** in Sonarr, Radarr, Prowlarr and Seerr.
- **Anime checks** in Sonarr and Seerr.
- **Fix it** for unused tags and anime series type, with **Undo** under Recent fixes.
- Findings can be dismissed, and the checks run once a day (Settings > General).
- Addresses must start with `http://` or `https://`.

## 0.1.0

- First release: web UI with login, Dashboard, Logs, Settings and Contact pages.
- Connections to each app, with **Test connection**.
- Health check in the web UI, at `/health`, and as a Docker health check.
- The Dashboard lists the checks coming in the next releases.
