<p align="center"><img src="src/web/logo.png" alt="Setuparr" width="280"></p>

# Setuparr

Checks your Unraid media setup against the TRaSH guides, then fixes what doesn't match with one button, or shows you how to fix it yourself.

> **Early version.** The web UI, login and connections to Sonarr, Radarr, Prowlarr, Seerr, Jellyfin and Plex work today. The checks below are being built and
> will arrive in the next releases. Watch or star the repository to hear when they land.

## What it will check

### Your Unraid setup

- Reads only the files you upload: the Docker templates and share settings from your flash drive. It never scans your server or needs the Docker socket. Passwords and keys in them are blanked as soon as they're opened.
- Hardlinks and instant moves: whether your download client and Sonarr/Radarr share one /data folder, as the TRaSH guide sets out.
- Folders that don't match between your download client and Sonarr/Radarr, so downloads never import.
- /mnt/user mixed with /mnt/cache or /mnt/diskX for the same files.
- PUID, PGID and UMASK that differ between containers.
- Share cache and mover settings for your data and appdata shares.

### Tags

- Indexers and download clients that only work for tagged items, leaving untagged ones with nowhere to search or download.
- Tags on indexers, download clients, delay and release profiles that nothing uses.
- Duplicate tags like 4k and 4K, and tags left over from old setups.
- Prowlarr indexers that miss the FlareSolverr tag, or don't reach an app because the tags don't match.
- Tags Seerr adds to requests that no longer exist in Sonarr or Radarr.

### Anime

- Anime series set to Standard instead of Anime in Sonarr, which searches with the wrong episode numbers.
- Anime outside your anime folder, quality profile or tag, and other shows inside them.
- Anime naming and quality profiles against the TRaSH anime guide.
- Indexers missing the anime category, and Seerr's anime settings.

### TRaSH guides

- Custom formats, quality profiles, quality sizes and naming in Sonarr and Radarr.
- Prowlarr's sync to your apps, Jellyfin and Plex settings.
- Each difference has a Fix it button that shows the change before making it and keeps a backup, or step-by-step instructions for the things it shouldn't change itself.

### Genres

- Your own rules, such as Kids, Documentaries or Stand-up going to their own folder, profile and tag, and a list of everything that doesn't follow them.

## Install on Unraid

1. Add the template from [`unraid/setuparr.xml`](unraid/setuparr.xml), or search Community Apps for Setuparr.
2. Keep the **Config** folder at `/mnt/user/appdata/setuparr`.
3. Start it, open the web UI (port 9893), and create your login with the setup code from the container's log.
4. Add your apps under **Settings** and press **Test connection** on each.

## Install with Docker

```bash
docker run -d --name setuparr \
  -p 9893:9893 \
  -v /path/to/appdata/setuparr:/config \
  ghcr.io/p1zzm1ssile/setuparr:latest
```

Or with Docker Compose:

```yaml
services:
  setuparr:
    image: ghcr.io/p1zzm1ssile/setuparr:latest
    container_name: setuparr
    ports:
      - 9893:9893
    volumes:
      - ./setuparr:/config
    restart: unless-stopped
```

Every setting is in the web UI. To pre-fill them on first start, see `.env.tmpl` for the environment variables.

## First run

1. Open `http://your-server:9893`.
2. Enter the **setup code** from Setuparr's log. It's printed when Setuparr starts (in Unraid, click the Setuparr icon and
   choose **Logs**; with Docker, run `docker logs setuparr`). This stops anyone else on your network, or anyone reaching
   it through a misconfigured proxy, from creating the account before you.
3. Create your login. The password needs at least 8 characters, with an uppercase letter, a lowercase letter, a number
   and a special character.

## Login and security

> ⚠️ **Keep Setuparr on your home network.** It can change the settings of Sonarr, Radarr, Prowlarr and Seerr and holds their API keys. We don't recommend putting it behind a reverse proxy
> (Nginx Proxy Manager, SWAG, Traefik, Cloudflare Tunnel and the like) for access from outside your network. To reach
> it remotely, use a VPN such as WireGuard or Tailscale.

- The password is stored as a scrypt hash in `/config/auth.json`, never in plain text.
- After 5 wrong passwords, that address is locked out for 5 minutes.
- **Skip login on my local network** (Settings > General) lets devices on the networks you list in without a password.
  It never applies to requests that arrive through a reverse proxy.
- Changing an app's address needs its API key entered again, so a saved key can't be sent somewhere new.
- Saved API keys are never sent back to the browser, and `settings.json` and `auth.json` are only readable by the
  account Setuparr runs as.
- **Forgotten your password?** Delete `auth.json` from the config folder and restart Setuparr. It will ask you to create
  a login again, with a new setup code in the log. Your settings are kept.

## Health check

- **HTTP:** `GET /health` returns the status as JSON, with HTTP 200 when healthy and 503 when not.
- **Command line:** `docker exec setuparr python healthcheck.py` exits with 0 when healthy, 1 when not.
- **Docker:** the image includes a `HEALTHCHECK`, so `docker ps` shows `healthy` or `unhealthy`.

## Contact

Bugs and ideas: [open an issue](https://github.com/p1zzm1ssile/Setuparr/issues/new?title=%5BSetuparr%5D%20). For anything
private, email [p1zzm1ssilerepo@gmail.com](mailto:p1zzm1ssilerepo@gmail.com?subject=Setuparr). Both are also on the Contact page in the web UI.

## Licence

MIT. See [LICENSE](LICENSE).
