#!/bin/sh
# Start Setuparr as PUID:PGID (Unraid's nobody:users, 99:100, unless set) with UMASK, rather than as root.
set -e

PUID=${PUID:-99}
PGID=${PGID:-100}
UMASK=${UMASK:-022}

case "$PUID$PGID" in
  *[!0-9]*) echo "PUID and PGID must be numbers (got PUID=$PUID PGID=$PGID)" >&2; exit 1 ;;
esac
umask "$UMASK"

if [ "$(id -u)" = "0" ]; then
  mkdir -p "$CONFIG_DIR"
  # Hand the config folder to PUID:PGID, but only walk it when something in it belongs to someone else
  if [ -n "$(find "$CONFIG_DIR" \( \! -user "$PUID" -o \! -group "$PGID" \) -print -quit)" ]; then
    echo "Giving $CONFIG_DIR to $PUID:$PGID"
    chown -R "$PUID:$PGID" "$CONFIG_DIR"
  fi
  exec su-exec "$PUID:$PGID" "$@"
fi

# Already started as a user (docker run --user): run as that user
exec "$@"
