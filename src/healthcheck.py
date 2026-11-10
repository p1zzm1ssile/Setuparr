"""Check whether Setuparr is working. Exits 0 when healthy, 1 when not.

Run it inside the container with: docker exec setuparr /entrypoint.sh python3 healthcheck.py
(through the entrypoint, so it runs as PUID:PGID like Setuparr and never leaves root-owned files in /config)
"""

import sys
from utils.status import STATUS_FILE, health_from_file


def main():
    try:
        result = health_from_file()
    except FileNotFoundError:
        print(f"UNHEALTHY: {STATUS_FILE} not found, Setuparr has not started")
        return 1
    except (OSError, ValueError) as e:
        print(f"UNHEALTHY: couldn't read {STATUS_FILE} ({e})")
        return 1

    print("HEALTHY" if result["healthy"] else "UNHEALTHY")
    for problem in result["problems"]:
        print(f"  ! {problem}")
    return 0 if result["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
