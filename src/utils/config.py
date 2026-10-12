"""Setuparr settings.

Every setting is described once in FIELDS: its tab in the web UI, its label, its
type, the environment variable it can come from, and its default. Settings live
in settings.json in CONFIG_DIR and are edited in the web UI. On first start,
settings.json is created from the environment variables.

The rest of Setuparr reads plain module attributes (config.sonarr_url and so on).
apply() fills those in from the saved values, so a change made in the web UI
takes effect without a restart.
"""

import ipaddress
import json
import os
import tempfile
import threading
import time

from dotenv import load_dotenv

import app_info

load_dotenv()


def ensure_http(url):
    url = (url or "").strip()
    return url if url.startswith(("http://", "https://")) else "http://" + url


def split_labels(value):
    if isinstance(value, list):
        value = ",".join(value)
    return [l.strip() for l in (value or "").split(",") if l.strip()]


def env_bool(*names, default=False):
    for name in names:
        value = os.getenv(name)
        if value is not None and value != "":
            return value.strip().lower() == "true"
    return default


def env_str(*names, default=""):
    for name in names:
        value = os.getenv(name)
        if value is not None:
            return value
    return default


def env_int(*names, default=0):
    value = env_str(*names, default=None)
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


# Only set by environment, since they're needed before the web UI can run
config_dir = os.getenv("CONFIG_DIR") or ("/config" if os.path.isdir("/config") else "config")
settings_file = os.path.join(config_dir, "settings.json")
log_dir = os.path.join(config_dir, "logs")
log_file = os.path.join(log_dir, f"{app_info.SLUG}.log")
log_output = os.getenv("LOG_OUTPUT", "all").lower()
web_host = os.getenv("WEB_HOST", "http://localhost").lower()
web_port = int(os.getenv("WEB_PORT", app_info.PORT))

TABS = {"general": "General", **{key: label for key, label, *_ in app_info.SERVICES}}


def F(key, tab, label, type, default, help="", section="", **extra):
    """Describe one setting. type is bool, int, text, secret, select or labels."""
    return dict(key=key, tab=tab, label=label, type=type, default=default, help=help, section=section, **extra)


FIELDS = [
    *app_info.extra_fields(F, env_str, env_bool, env_int),

    F("log_level", "general", "Log level", "select", env_str("LOG_LEVEL", default="INFO").upper(),
      section="Logs", options=["INFO", "WARNING", "ERROR"]),
    F("log_lines", "general", "Log lines shown", "int", env_int("LOG_LINES", default=500),
      section="Logs", min=50, max=10000),
    F("request_timeout", "general", "Request timeout (seconds)", "int",
      env_int("REQUEST_TIMEOUT", default=30), section="Logs", min=1),
    F("auth_lan_bypass", "general", "Skip login on my local network", "bool",
      env_bool("AUTH_LAN_BYPASS", default=False), section="Login",
      help="Don't ask for a password from the networks below. Never applies to requests that come through a reverse proxy."),
    F("local_networks", "general", "My local networks", "labels",
      env_str("LOCAL_NETWORKS", default="192.168.0.0/16, 10.0.0.0/8"), section="Login",
      help="Addresses that skip the login, separated by commas. 172.16.0.0/12 isn't included by default because Docker uses it, "
           "and traffic forwarded by Docker can appear to come from there."),

    *[
        field
        for service, label, port, key_help in app_info.SERVICES
        for field in (
            F(f"{service}_enabled", service, f"Use {label}", "bool",
              env_bool(f"{service.upper()}_ENABLED", default=bool(os.getenv(f"{service.upper()}_API_KEY"))),
              section="Connection"),
            F(f"{service}_url", service, "Address", "text",
              env_str(f"{service.upper()}_URL", default=f"localhost:{port}"), section="Connection",
              help=f"IP address or hostname with port, e.g. 192.168.1.10:{port}"),
            F(f"{service}_api_key", service, "Token" if service == "plex" else "API key", "secret",
              env_str(f"{service.upper()}_API_KEY"), section="Connection", help=key_help),
        )
    ],
]

FIELDS_BY_KEY = {f["key"]: f for f in FIELDS}
SECRET_KEYS = {f["key"] for f in FIELDS if f["type"] == "secret"}

# The address each secret is sent to. A saved secret is never reused with a new
# address, or anyone with access could send it to a server of their choosing.
SECRET_URLS = {f"{service}_api_key": f"{service}_url" for service, *_ in app_info.SERVICES}


def secrets_needing_reentry(changes: dict) -> dict:
    """{secret key: reason} for saved secrets whose address is being changed."""
    problems = {}
    for secret, url in SECRET_URLS.items():
        new_url = changes.get(url)
        if new_url is None or str(new_url).strip() == str(values.get(url, "")).strip():
            continue
        if changes.get(secret) is None and values.get(secret):
            label = FIELDS_BY_KEY[secret]["label"]
            problems[secret] = f"{label}: enter it again, since the address changed"
    return problems


_lock = threading.RLock()
load_error = None
values = {f["key"]: f["default"] for f in FIELDS}


def coerce(field, value):
    """Turn a submitted value into the right type, or raise ValueError with a reason."""
    t = field["type"]
    if t == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        raise ValueError("must be on or off")
    if t == "int":
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError("must be a whole number")
        if "min" in field and number < field["min"]:
            raise ValueError(f"must be at least {field['min']}")
        if "max" in field and number > field["max"]:
            raise ValueError(f"must be at most {field['max']}")
        return number
    if t == "select":
        if value not in field["options"]:
            raise ValueError(f"must be one of {', '.join(field['options'])}")
        return value
    value = "" if value is None else str(value).strip()
    if len(value) > 2000:
        raise ValueError("is too long")
    if t == "text" and field["key"].endswith("_url") and any(c in value for c in " <>\"'"):
        raise ValueError("isn't a valid address")
    if field["key"] == "local_networks":
        for item in split_labels(value):
            try:
                ipaddress.ip_network(item, strict=False)
            except ValueError:
                raise ValueError(f"has an invalid network: {item} (use a form like 192.168.1.0/24)")
    if field.get("paths"):
        for item in split_labels(value):
            if not item.startswith("/") or item.rstrip("/") == "":
                raise ValueError(f"must be full folders inside the container, like /movies: {item}")
    return value


def parse_networks(value):
    networks = []
    for item in split_labels(value):
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            pass
    return networks


def resolve(raw):
    """Every attribute the rest of Setuparr reads, worked out from the raw settings."""
    resolved = dict(raw)
    for service, *_ in app_info.SERVICES:
        resolved[f"{service}_url"] = ensure_http(raw[f"{service}_url"]).rstrip("/")
    resolved["local_networks"] = parse_networks(raw["local_networks"])
    return resolved


def apply(target, raw):
    """Set the attributes the rest of Setuparr reads, on target, from raw values.

    Background threads read them without a lock, so each attribute is only ever
    set to its final value: never, even briefly, to the raw text it comes from.
    """
    for key, value in resolve(raw).items():
        setattr(target, key, value)


def load():
    """Read settings.json, creating it from the environment on first start."""
    global values, load_error
    with _lock:
        saved = {}
        if os.path.exists(settings_file):
            try:
                with open(settings_file) as f:
                    saved = json.load(f)
            except (OSError, ValueError) as e:
                # Keep the broken file for the user to fix, rather than overwrite it later
                broken = f"{settings_file}.broken-{int(time.time())}"
                os.replace(settings_file, broken)
                load_error = f"settings.json couldn't be read ({e}). It was moved to {broken} and defaults are being used"
                saved = {}

        merged = {f["key"]: f["default"] for f in FIELDS}
        for key, value in saved.items():
            if key in FIELDS_BY_KEY:
                try:
                    merged[key] = coerce(FIELDS_BY_KEY[key], value)
                except ValueError:
                    pass
        values = merged
        apply(globals_namespace, values)

        if not os.path.exists(settings_file):
            _write(values)


def _write(data):
    os.makedirs(config_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=config_dir, prefix=".settings-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)  # holds API keys
        os.replace(tmp, settings_file)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def update(changes: dict):
    """Validate and save changed settings. Returns {key: reason} for any rejected."""
    global values
    errors = {}
    clean = {}
    for key, value in changes.items():
        field = FIELDS_BY_KEY.get(key)
        if field is None:
            errors[key] = "isn't a setting"
            continue
        # Secrets are never sent to the browser; an untouched one comes back as None
        if field["type"] == "secret" and value is None:
            continue
        try:
            clean[key] = coerce(field, value)
        except ValueError as e:
            errors[key] = f"{field['label']} {e}"

    errors.update(secrets_needing_reentry(changes))
    if errors:
        return errors

    with _lock:
        new_values = {**values, **clean}
        _write(new_values)
        values = new_values
        apply(globals_namespace, values)
    return {}


def preview(changes: dict):
    """A copy of the current settings with unsaved changes applied, for testing a connection."""
    from types import SimpleNamespace

    problems = secrets_needing_reentry(changes)
    if problems:
        raise ValueError("; ".join(problems.values()))
    raw = dict(values)
    for key, value in changes.items():
        field = FIELDS_BY_KEY.get(key)
        if field is None or (field["type"] == "secret" and value is None):
            continue
        raw[key] = coerce(field, value)
    target = SimpleNamespace(**{k: v for k, v in globals().items() if not k.startswith("_")})
    apply(target, raw)
    return target


def public_values():
    """Settings safe to send to the browser: secrets are replaced by whether they're set."""
    with _lock:
        return {
            key: ({"set": bool(value)} if key in SECRET_KEYS else value)
            for key, value in values.items()
        }


class _Module:
    """Lets apply() set attributes on this module."""

    def __setattr__(self, key, value):
        globals()[key] = value

    def __getattr__(self, key):
        try:
            return globals()[key]
        except KeyError:
            raise AttributeError(key)


globals_namespace = _Module()
load()
