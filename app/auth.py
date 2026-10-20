"""Small yt-dlp authentication helpers; never store cookie contents in settings."""

import os
import stat
import warnings
from http.cookiejar import LoadError, MozillaCookieJar
from pathlib import Path


BROWSERS = ("chrome", "firefox", "edge", "safari", "brave", "chromium", "opera", "vivaldi", "whale")
AUTH_ERRORS = {"auth_invalid", "auth_file_unreadable", "auth_file_invalid"}
MAX_COOKIE_BYTES = 10 * 1024 * 1024


def normalize_auth(auth: dict | None) -> dict:
    if auth is None or auth == {}:
        return {}
    if not isinstance(auth, dict):
        raise ValueError("auth_invalid")
    source = auth.get("source")
    if source == "browser":
        browser, profile = auth.get("browser"), auth.get("profile", "")
        if browser not in BROWSERS or not isinstance(profile, str):
            raise ValueError("auth_invalid")
        profile = profile.strip()
        # :: is yt-dlp's Firefox container delimiter, not part of a profile.
        if "::" in profile or any(ord(c) < 32 for c in profile):
            raise ValueError("auth_invalid")
        return {"source": source, "browser": browser, "profile": profile}
    if source == "file":
        path = auth.get("path")
        if not isinstance(path, str) or any(ord(c) < 32 for c in path) or not Path(path).is_absolute():
            raise ValueError("auth_invalid")
        return {"source": source, "path": path}
    raise ValueError("auth_invalid")


def auth_arguments(auth: dict | None) -> list[str]:
    auth = normalize_auth(auth)
    if not auth:
        return []
    if auth["source"] == "file":
        return ["--cookies", auth["path"]]
    profile = auth["profile"]
    return ["--cookies-from-browser", auth["browser"] + (f":{profile}" if profile else "")]


def prepare_auth(auth: dict | None, private_folder: Path | None = None) -> dict:
    """Use a private per-operation snapshot: yt-dlp writes back its cookie jar."""
    auth = normalize_auth(auth)
    if not auth or auth["source"] != "file":
        return auth
    if private_folder is None:
        raise ValueError("auth_invalid")
    try:
        with Path(auth["path"]).open("rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError("auth_file_invalid")
            data = source.read(MAX_COOKIE_BYTES + 1)
    except OSError:
        raise ValueError("auth_file_unreadable") from None
    if len(data) > MAX_COOKIE_BYTES or data.splitlines()[:1] not in (
        [b"# Netscape HTTP Cookie File"], [b"# HTTP Cookie File"],
    ):
        raise ValueError("auth_file_invalid")
    snapshot = private_folder / "session.txt"
    with open(os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as target:
        target.write(data)
    # Validate with the standard Netscape reader; never expose a malformed row
    # (which can contain a session secret) in an exception or warning.
    try:
        jar = MozillaCookieJar(str(snapshot))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            jar.load(ignore_discard=True, ignore_expires=True)
        if not any(c.domain.lstrip(".") == "youtube.com" or c.domain.endswith(".youtube.com") for c in jar):
            raise ValueError("auth_file_invalid")
    except (LoadError, ValueError, UnicodeError):
        snapshot.unlink(missing_ok=True)
        raise ValueError("auth_file_invalid") from None
    return {"source": "file", "path": str(snapshot)}
