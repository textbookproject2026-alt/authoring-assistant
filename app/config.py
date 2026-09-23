"""
Where the app keeps its own files, and how it finds out about itself.

Everything lives under ~/Library/Application Support/Authoring Assistant, which
is the normal place for a Mac application to keep things. Nothing is ever written
into the author's vault except the chapter and the glossary they approve.
"""

import json
import os
import sys

APP_NAME = "Authoring Assistant"

SUPPORT_DIR = os.path.expanduser(
    f"~/Library/Application Support/{APP_NAME}"
)
KEY_FILE = os.path.join(SUPPORT_DIR, "deepseek.key")
STATE_FILE = os.path.join(SUPPORT_DIR, "state.json")
RUNTIME_FILE = os.path.join(SUPPORT_DIR, "runtime.json")
LOG_FILE = os.path.join(SUPPORT_DIR, "log.txt")

# Where the key used to live, before the tool became a proper application.
LEGACY_KEY_FILE = os.path.expanduser("~/.authoring-assistant/deepseek.key")


def ensure_dir():
    os.makedirs(SUPPORT_DIR, mode=0o700, exist_ok=True)
    return SUPPORT_DIR


def bundled():
    """True when running from inside the .app rather than from a checkout."""
    return "/Contents/Resources" in os.path.abspath(__file__)


def bundle_version():
    """Read the version the build script stamped into the bundle."""
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (os.path.join(here, "VERSION"),
                      os.path.join(os.path.dirname(here), "VERSION")):
        try:
            with open(candidate, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            continue
    return "development"


def build_id():
    """Which copy of the app the files on disk are: the version, and a
    fingerprint of the code and the page.

    The version alone isn't enough, because every build is stamped 1.0.0 unless
    it is told otherwise. A running copy reads this once, as it starts; the
    page is stamped with it when it is served, from the files as they are then.
    If a newer build has replaced the files under a copy still running, the two
    differ, and the page says so instead of asking for something the running
    copy doesn't know about.
    """
    import hashlib

    here = os.path.dirname(os.path.abspath(__file__))
    digest = hashlib.sha256()
    found = []
    for folder, dirs, files in os.walk(here):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith((".py", ".js", ".html", ".css")):
                found.append(os.path.join(folder, name))
    for full in sorted(found):
        digest.update(os.path.relpath(full, here).encode("utf-8") + b"\0")
        try:
            with open(full, "rb") as fh:
                digest.update(fh.read())
        except OSError:
            continue
        digest.update(b"\0")
    return f"{bundle_version()}+{digest.hexdigest()[:12]}"


def read_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(**changes):
    state = read_state()
    state.update(changes)
    ensure_dir()
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, STATE_FILE)
    return state


def seen_welcome():
    return bool(read_state().get("seen_welcome"))


def mark_welcome_seen():
    write_state(seen_welcome=True)


# --- making sure only one copy is running -----------------------------------

def write_runtime(port, token):
    ensure_dir()
    tmp = RUNTIME_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"port": port, "token": token, "pid": os.getpid()}, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, RUNTIME_FILE)


def read_runtime():
    try:
        with open(RUNTIME_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("port") and data.get("token"):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def clear_runtime():
    try:
        os.remove(RUNTIME_FILE)
    except OSError:
        pass


def running_elsewhere():
    """If another copy is already serving, return its address. Otherwise None.

    Double-clicking the app a second time should simply bring the page back,
    not start a second copy quietly fighting over the same files.
    """
    import urllib.error
    import urllib.request

    info = read_runtime()
    if not info:
        return None
    url = f"http://127.0.0.1:{info['port']}/api/alive?t={info['token']}"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as resp:
            if resp.status == 200:
                return f"http://127.0.0.1:{info['port']}/?t={info['token']}"
    except (urllib.error.URLError, OSError, ValueError):
        pass
    clear_runtime()
    return None


def redirect_output():
    """Send anything printed to a log file, since there is no window to show it."""
    ensure_dir()
    try:
        fh = open(LOG_FILE, "a", buffering=1, encoding="utf-8")
        sys.stdout = fh
        sys.stderr = fh
    except OSError:
        pass
