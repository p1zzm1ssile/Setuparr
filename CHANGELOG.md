# Changelog

## 0.6.0

- **More of Unraid's own settings are checked:**
  - **Ports:** two containers on the same host port, or a container on the port Unraid's web UI uses.
  - **Start order:** a container that goes through another's network (like qBittorrent through Gluetun) starting
    before it, or without it, and media containers that don't start by themselves.
  - **Disks:** disabled, emulated, missing or unmountable disks, read and write errors, no parity drive, parity checks
    not scheduled, and a pool or the flash drive over 90% full.
  - **Docker:** log rotation turned off.
  - **Alerts** only shown in the web UI, **NTP** turned off, and containers on a different **time zone** from Unraid.
  - **appdata shared over SMB** so anyone on your network can read it.
  - **The mover:** running every hour, downloads or media shares with no minimum free space, or never leaving the cache.
  - **Appdata Backup:** no schedule or destination, backups on the same pool as appdata, the flash drive not backed up,
    and media containers skipped.
  - **Plugins** made for a different Unraid version, and containers given a GPU the server doesn't have.
- The terminal command on the Unraid page now also sends `ident.cfg`, `dynamix.cfg`, Appdata Backup's settings,
  `disks.ini` and Docker's start order. Only what the checks use is kept: never disk serial numbers, IP addresses,
  email settings or time zones. Host ports are now kept too. **Get the command again** and run it to get the new checks.
- Choosing files by hand now accepts `.plg` files, which were left out before.
- **Sonarr and Radarr:** their own health warnings, no login or no password on your network, Set Permissions turned
  on, quality profiles (no custom formats, all scores 0, a minimum score nothing can reach, no upgrades) and backups
  that are off or saved where they're lost on update. Shown under **Sonarr & Radarr** on the Dashboard.

## 0.5.0

- **More Unraid checks, from Unraid's own docs and Fix Common Problems as well as TRaSH:**
  - Hard link support (Tunable (support Hard Links)) turned off in Global Share Settings.
  - appdata going through Unraid's slower FUSE layer when it could be an exclusive share.
  - Docker's image or folder on the array, or Docker's folder going through the FUSE layer.
  - macvlan with bridging on, which crashes Unraid 6.12.10 and earlier.
- **Plugins:** plugins and containers that Community Applications has deprecated or blacklisted (its moderation list
  is downloaded once a day), and Fix Common Problems or Appdata Backup not installed.
- **Hardware:** Nvidia, Intel and AMD GPUs, Realtek network cards and Coral accelerators without their driver plugin,
  and Plex, Jellyfin or Emby not given the GPU for transcoding.
- The terminal command on the Unraid page now also sends `docker.cfg`, `share.cfg`, `network.cfg` and the plugin
  files, the Unraid version, and `lspci -n`'s PCI IDs. Only the settings these checks use are kept, never IP addresses.
  **Get the command again** and run it to get the new checks.

## 0.4.2

- **qBittorrent 5.2 works.** qBittorrent 5.2 answers a correct sign-in with no text, where older versions said "Ok.",
  so Setuparr said it had turned down the username or password even when they were right. Both answers are now
  understood, as is 5.2's way of saying a password is wrong.

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
