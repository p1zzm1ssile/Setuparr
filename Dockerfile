# Building from vanilla alpine base, as the official Python alpine image lags behind on security fixes.
# Pinned to a release so the same commit always builds the same way; Dependabot proposes newer ones.
FROM alpine:3.22

LABEL author="p1zzm1ssile"
LABEL org.opencontainers.image.source="https://github.com/p1zzm1ssile/Setuparr"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.description='Setuparr: checks your Unraid media setup (Sonarr, Radarr, Prowlarr, Seerr, Jellyfin, Plex) against the TRaSH guides.'

WORKDIR /app

RUN apk add --no-cache python3 py3-pip su-exec
COPY ./src/requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r /app/requirements.txt
COPY ./src .

# Settings, the web UI login and logs live here. Map it to appdata to keep them.
# Setuparr runs as PUID:PGID with UMASK, like other Unraid containers: nobody:users (99:100) unless set.
ENV CONFIG_DIR=/config PUID=99 PGID=100 UMASK=022
RUN mkdir -p /config
VOLUME /config
EXPOSE 9893

# As PUID:PGID too, so nothing it touches in /config ends up owned by root
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
  CMD ["/entrypoint.sh", "python3", "healthcheck.py"]
COPY ./entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "main.py"]
