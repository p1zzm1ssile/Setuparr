# Changelog

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
