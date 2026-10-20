"""Daily stable yt-dlp checks and transactional, worker-only installation."""

import json
import os
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QLockFile

from app.dependencies import (
    DependencyError, asset_checksum, cached_tools, component_sources, copy_cached_tools,
    detect_platform, fetch, release_info, sha256,
)


DAY = 24 * 60 * 60


def read_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text("utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(path: Path, state: dict):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            json.dump(state, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def operation_lock(root: Path):
    """Held by workers through tool exit, including across app instances."""
    folder = root / detect_platform().key
    folder.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(folder / "yt-dlp-operation.lock"))
    lock.setStaleLockTime(0)
    while not lock.tryLock(100):
        if lock.error() != QLockFile.LockError.LockFailedError:
            raise DependencyError("Cannot lock the private yt-dlp installation.")
    try:
        yield
    finally:
        lock.unlock()


def check_for_updates(root: Path, *, now=None) -> dict:
    """Metadata only; never replace a binary from the startup process."""
    now = time.time() if now is None else now
    platform = detect_platform()
    folder = root / platform.key
    folder.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(folder / "yt-dlp-check.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        return {"status": "busy"}
    try:
        state_path = folder / "yt-dlp-update.json"
        state = read_state(state_path)
        checked = state.get("checked_at")
        if isinstance(checked, (int, float)) and 0 <= now - checked < DAY:
            return check_result(folder, state, platform.suffix)
        # Count failed attempts too, so restarting offline never causes a loop.
        state["checked_at"] = now
        write_state(state_path, state)
        try:
            release = release_info("yt-dlp/yt-dlp", "latest", timeout=7)
            version = release["tag_name"]
            if release.get("draft") or release.get("prerelease") or not re.fullmatch(r"[0-9][0-9A-Za-z._-]{0,99}", version):
                raise DependencyError("Invalid stable yt-dlp release.")
            asset_name = next(iter(component_sources(platform)["yt-dlp"][2]))
            assets = {asset["name"]: asset for asset in release["assets"]}
            asset = assets[asset_name]
            digest = asset_checksum(asset, assets)
            target = {"version": version, "name": asset_name, "url": asset["browser_download_url"], "sha256": digest}
            validate_target(target, platform)
            state["target"] = target
            state.pop("error", None)
        except (OSError, ValueError, KeyError, TypeError, DependencyError):
            state["error"] = True
        write_state(state_path, state)
        return check_result(folder, state, platform.suffix)
    finally:
        lock.unlock()


def check_result(folder, state, suffix):
    if state.get("error"):
        return {"status": "check_failed"}
    target = state.get("target")
    if not isinstance(target, dict):
        return {"status": "current"}
    manifest = read_state(folder / "yt-dlp" / "installed.json")
    files = manifest.get("files", {})
    digest = files.get("yt-dlp" + suffix) if isinstance(files, dict) else None
    return {"status": "current" if digest == target.get("sha256") else "available", "version": target.get("version", "")}


def validate_target(target: dict, platform):
    expected_name = next(iter(component_sources(platform)["yt-dlp"][2]))
    if (target.get("name") != expected_name
            or not isinstance(target.get("version"), str)
            or not re.fullmatch(r"[0-9][0-9A-Za-z._-]{0,99}", target["version"])
            or not re.fullmatch(r"[a-fA-F0-9]{64}", target.get("sha256", ""))
            or target.get("url") != f"https://github.com/yt-dlp/yt-dlp/releases/download/{target['version']}/{expected_name}"):
        raise DependencyError("Invalid official yt-dlp update metadata.")


def apply_update(root: Path, tools: dict, report, env: dict, *, now=None):
    """Caller holds operation_lock; a failed install keeps/restores the old tool."""
    now = time.time() if now is None else now
    platform = detect_platform()
    folder = root / platform.key
    target = read_state(folder / "yt-dlp-update.json").get("target")
    if not isinstance(target, dict):
        return
    active, previous = folder / "yt-dlp", folder / "yt-dlp.previous"
    name = "yt-dlp" + platform.suffix
    names = {name}
    try:
        validate_target(target, platform)
        if sha256(tools["yt-dlp"]) == target["sha256"]:
            return
        failed = read_state(folder / "yt-dlp-update-failed.json")
        if failed.get("sha256") == target["sha256"] and now < failed.get("retry_after", 0):
            report({"type": "update", "status": "failed", "version": target["version"]})
            return
        report({"type": "update", "status": "installing", "version": target["version"]})
        with tempfile.TemporaryDirectory(prefix=".install-", dir=folder) as temporary:
            stage = Path(temporary)
            binary = stage / name
            fetch({"browser_download_url": target["url"]}, binary, target["sha256"],
                  lambda percent: report({"type": "dependency", "component": "yt-dlp", "percent": percent}))
            binary.chmod(0o755)
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            # A file avoids a stuck child keeping a stdout pipe open after a
            # timeout; the GUI's process group/Job still contains all children.
            with tempfile.TemporaryFile() as output:
                subprocess.run([str(binary), "--version"], stdin=subprocess.DEVNULL, stdout=output,
                               stderr=subprocess.DEVNULL, timeout=20, check=True, env=env, creationflags=flags)
                output.seek(0)
                version = output.read(256).decode("utf-8", "replace").strip()
            if version != target["version"]:
                raise DependencyError("The new yt-dlp executable reported an unexpected version.")
            manifest = {"repository": "yt-dlp/yt-dlp", "version": target["version"],
                        "assets": [target["name"]], "files": {name: target["sha256"]}}
            (stage / "installed.json").write_text(json.dumps(manifest), encoding="utf-8")
            # Complete the backup before touching any currently installed file.
            copy_cached_tools(active, previous, names)
            try:
                copy_cached_tools(stage, active, names)
            except BaseException:
                copy_cached_tools(previous, active, names)
                raise
        try:
            (folder / "yt-dlp-update-failed.json").unlink(missing_ok=True)
        except OSError:
            pass
        report({"type": "update", "status": "installed", "version": target["version"]})
    except (OSError, ValueError, TypeError, KeyError, DependencyError, subprocess.SubprocessError):
        # No network/update failure should disable a valid installed downloader.
        try:
            write_state(folder / "yt-dlp-update-failed.json", {"sha256": target.get("sha256"), "retry_after": now + DAY})
        except OSError:
            pass
        if not cached_tools(active, names):
            raise DependencyError("yt-dlp update failed and the previous installation could not be restored.")
        report({"type": "update", "status": "failed", "version": target.get("version", "")})
