"""Small yt-dlp authentication helpers; never store cookie contents in settings."""

import os
import stat
import subprocess
import tempfile
import time
import warnings
from http.cookiejar import LoadError, MozillaCookieJar
from pathlib import Path


BROWSERS = ("chrome", "firefox", "edge", "safari", "brave", "chromium", "opera", "vivaldi", "whale")
AUTH_ERRORS = {"auth_invalid", "auth_file_unreadable", "auth_file_invalid", "auth_import_required",
               "auth_session_expired", "auth_no_session", "auth_import_failed", "auth_storage_failed", "auth_reimport_required"}
MAX_COOKIE_BYTES = 10 * 1024 * 1024
LOGIN_COOKIES = {"SID", "SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID",
                 "__Secure-1PSID", "__Secure-3PSID", "LOGIN_INFO"}


def youtube_cookie(cookie) -> bool:
    domain = cookie.domain.lstrip(".").lower()
    return domain == "youtube.com" or domain.endswith(".youtube.com")


def reimport_needed(message: str) -> bool:
    text = message.lower().replace("’", "'")
    return any(part in text for part in (
        "cookies are no longer valid", "cookies have expired", "session expired",
        "sign in to confirm", "login required", "authentication required", "use --cookies",
    ))


def private_directory(folder: Path) -> None:
    if folder.is_symlink():
        raise ValueError("auth_storage_failed")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        folder.chmod(0o700)
    else:
        # Restrict the app's own directory, not any browser/user-selected folder.
        import csv
        try:
            result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                                    check=True, capture_output=True, text=True, timeout=10,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            sid = next(csv.reader(result.stdout.splitlines()))[1]
            if not sid.startswith("S-1-"):
                raise ValueError()
            subprocess.run(["icacls", str(folder), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"],
                           check=True, capture_output=True, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, ValueError, IndexError, StopIteration, subprocess.SubprocessError):
            raise ValueError("auth_storage_failed") from None


def saved_session(folder: Path) -> dict:
    """Return an app-owned cookie file; never fall back to reading the browser."""
    path = folder / "cookies.txt"
    if not path.is_file():
        raise ValueError("auth_import_required")
    if path.stat().st_size > MAX_COOKIE_BYTES:
        raise ValueError("auth_file_invalid")
    login = [c for c in session_jar(path) if youtube_cookie(c) and c.name in LOGIN_COOKIES]
    if not login:
        raise ValueError("auth_import_required")
    if not any(not c.is_expired(time.time()) for c in login):
        raise ValueError("auth_session_expired")
    return {"source": "file", "path": str(path)}


def session_jar(path: Path) -> MozillaCookieJar:
    jar = MozillaCookieJar(str(path))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except (LoadError, ValueError, UnicodeError, OSError):
            raise ValueError("auth_file_invalid") from None
    for cookie in jar:
        if cookie.expires == 0:
            cookie.expires, cookie.discard = None, True
    return jar


def store_session(source: Path, folder: Path, workspace: Path | None = None) -> None:
    """Validate and atomically replace only this app's session; keep old on failure."""
    private_directory(folder)
    with tempfile.TemporaryDirectory(prefix=".import-", dir=workspace) as temporary:
        snapshot = prepare_auth({"source": "file", "path": str(source)}, Path(temporary))
        jar = session_jar(Path(snapshot["path"]))
        cookies = [c for c in jar if youtube_cookie(c)]
        login = [c for c in cookies if c.name in LOGIN_COOKIES]
        if not login:
            raise ValueError("auth_no_session")
        if not any(not c.is_expired(time.time()) for c in login):
            raise ValueError("auth_session_expired")
        fd, filename = tempfile.mkstemp(prefix=".session-", dir=folder)
        os.close(fd)
        clean = MozillaCookieJar(filename)
        for cookie in cookies:
            clean.set_cookie(cookie)
        try:
            clean.save(ignore_discard=True, ignore_expires=True)
            os.replace(clean.filename, folder / "cookies.txt")
        finally:
            Path(clean.filename).unlink(missing_ok=True)


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
