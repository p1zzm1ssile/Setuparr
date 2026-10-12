# Building from vanilla alpine base, as the official Python alpine image lags behind on security fixes
FROM alpine:latest

LABEL author="p1zzm1ssile"
LABEL org.opencontainers.image.source="https://github.com/p1zzm1ssile/Setuparr"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.description='Setuparr: checks your Unraid media setup (Sonarr, Radarr, Prowlarr, Seerr, Jellyfin, Plex) against the TRaSH guides.'

WORKDIR /app

RUN apk add --no-cache python3 py3-pip
COPY ./src/requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r /app/requirements.txt
COPY ./src .

# Settings, the web UI login and logs live here. Map it to appdata to keep them.
ENV CONFIG_DIR=/config
RUN mkdir -p /config
VOLUME /config
EXPOSE 9893

HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
  CMD ["python3", "healthcheck.py"]
CMD ["python3", "main.py"]
