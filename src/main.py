import signal
import sys
import threading
import time

import app_info
import utils.config as config
import utils.status as status
import utils.web as web
from utils.logger import logger


def problems():
    return [config.load_error] if config.load_error else []


def main():
    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    logger.log("STARTUP", f"Starting {app_info.NAME} web UI")
    threading.Thread(target=web.serve, daemon=True).start()
    logger.log("STARTUP", f"{app_info.NAME} is running. Open the web UI on port {config.web_port}")

    while True:
        status.heartbeat(problems())
        time.sleep(60)


def handle_sigint(signum, frame):
    sys.exit(0)


if __name__ == "__main__":
    logger.info(f"{app_info.NAME} version {app_info.VERSION} starting")
    main()
