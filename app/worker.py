"""Headless worker, launched and contained by the GUI's QProcess."""

import io
import base64
import json
import os
import signal
import subprocess
import sys
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from app.auth import prepare_auth
from app.dependencies import DependencyError, ensure_dependencies
from app.downloader import build_arguments, build_inspection_arguments, error_message, parse_output, validate_url
from app.media import summarize_info


class Cancelled(BaseException):
    pass


def emit(event: dict):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def connect_stdio():
    # PyInstaller --windowed sets sys.std* to None. QProcess still supplies OS
    # pipe handles, which we explicitly reopen in worker mode only.
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        for attribute, constant, flags, mode in (
            ("stdin", -10, os.O_RDONLY, "rb"),
            ("stdout", -11, os.O_WRONLY, "wb"),
            ("stderr", -12, os.O_WRONLY, "wb"),
        ):
            if getattr(sys, attribute) is None:
                handle = kernel.GetStdHandle(constant & 0xFFFFFFFF)
                fd = msvcrt.open_osfhandle(handle, flags | os.O_BINARY)
                setattr(sys, attribute, io.TextIOWrapper(os.fdopen(fd, mode), encoding="utf-8", write_through=True))
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def tool_environment(components: Path) -> dict[str, str]:
    env = os.environ.copy()
    # Do not let our PyInstaller runtime contaminate a different bundled tool.
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for variable in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        original = env.get(variable + "_ORIG")
        if original is not None:
            env[variable] = original
        else:
            env.pop(variable, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["DENO_DIR"] = str(components / "deno-cache")
    if os.name == "nt" and getattr(sys, "frozen", False):
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    return env


def publish_file(source: Path, downloads: Path) -> Path:
    """Exclusive publication: never overwrite or delete an existing user file."""
    for index in range(10000):
        name = source.name if index == 0 else f"{source.stem} ({index}){source.suffix}"
        target = downloads / name
        try:
            os.link(source, target)
            return target
        except FileExistsError:
            continue
        except OSError:
            # FAT/exFAT may not support links. Reserve a name exclusively, then
            # atomically rename on the same volume, replacing only our reservation.
            try:
                with target.open("xb"):
                    pass
            except FileExistsError:
                continue
            try:
                os.replace(source, target)
                return target
            except BaseException:
                target.unlink(missing_ok=True)  # Only the file we created with xb.
                raise
    raise OSError("Too many files with the same name in Downloads.")


def run_download(request: dict) -> int:
    url = validate_url(request["url"])
    downloads = Path(request["downloads"])
    staging = Path(request["staging"]).resolve()
    components = Path(request["components"])
    auth = prepare_auth(request.get("auth"), Path(request["auth_folder"]) if request.get("auth_folder") else None)
    tools = ensure_dependencies(components, emit)
    emit({"type": "extracting"})
    video, audio = request.get("video", True), request.get("audio", True)
    args = [str(tools["yt-dlp"]), *build_arguments(url, staging, tools, request.get("container", "auto"), request.get("format_id"), video=video, audio=audio, auth=auth)]
    lines = deque(maxlen=25)
    result = None
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
        env=tool_environment(components), creationflags=flags,
    ) as process:
        for raw in process.stdout:
            event = parse_output(raw.rstrip("\r\n"))
            if event["type"] == "file":
                result = Path(event["path"]).resolve()
            elif event["type"] == "log":
                lines.append(event["text"])
                # No unbounded log stream in the GUI, but warnings may help the user.
            else:
                emit(event)
        code = process.wait()
    if code:
        raise RuntimeError(error_message(list(lines), code))
    if result is None:
        raise RuntimeError("No video was downloaded. Live/upcoming streams are not supported.")
    if not result.is_relative_to(staging) or not result.is_file() or result.suffix not in {".mp4", ".mkv", ".webm", ".m4a", ".opus", ".mp3", ".ogg", ".aac"}:
        raise RuntimeError("yt-dlp did not produce a valid media file.")
    if not video or not audio:
        probe = subprocess.run([str(tools["ffprobe"]), "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(result)],
                               capture_output=True, text=True, encoding="utf-8", timeout=15, check=True,
                               env=tool_environment(components), creationflags=flags)
        present = {s["codec_type"] for s in json.loads(probe.stdout)["streams"]} & {"video", "audio"}
        expected = ({"video"} if video else set()) | ({"audio"} if audio else set())
        if present != expected:
            raise RuntimeError("Downloaded streams do not match the video/audio selection.")
    emit({"type": "saving"})
    target = publish_file(result, downloads)
    emit({"type": "success", "path": str(target)})
    return 0


def fetch_thumbnail(info: dict) -> str | None:
    thumbnails = info.get("thumbnails") or []
    # Prefer a small JPEG/PNG, which keeps the worker protocol and UI lightweight.
    candidates = sorted(thumbnails, key=lambda t: abs((t.get("width") or 320) - 480))
    urls = [t.get("url") for t in candidates] + [info.get("thumbnail")]
    for url in [url for url in urls if url][:3]:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not (host.endswith(".ytimg.com") or host in {"ytimg.com", "img.youtube.com"}):
            continue
        try:
            with urlopen(Request(url, headers={"User-Agent": "YTSlopDL/0.1"}), timeout=8) as response:
                data = response.read(2 * 1024 * 1024 + 1)
                if 0 < len(data) <= 2 * 1024 * 1024:
                    return base64.b64encode(data).decode("ascii")
        except OSError:
            continue
    return None


def run_inspection(request: dict) -> int:
    components = Path(request["components"])
    auth = prepare_auth(request.get("auth"), Path(request["auth_folder"]) if request.get("auth_folder") else None)
    tools = ensure_dependencies(components, emit)
    emit({"type": "extracting"})
    args = [str(tools["yt-dlp"]), *build_inspection_arguments(request["url"], tools, auth=auth)]
    info, lines = None, deque(maxlen=25)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                          env=tool_environment(components), creationflags=flags) as process:
        for raw in process.stdout:
            if raw.startswith("{"):
                info = json.loads(raw)
            else:
                lines.append(raw.strip())
        code = process.wait()
    if code:
        raise RuntimeError(error_message(list(lines), code))
    if not isinstance(info, dict):
        raise RuntimeError("yt-dlp returned no video information.")
    summary = summarize_info(info)
    summary["url"] = validate_url(request["url"])
    summary["thumbnail"] = fetch_thumbnail(info)
    emit({"type": "metadata", "info": summary})
    return 0


def main() -> int:
    connect_stdio()
    if os.name != "nt":
        def cancel(signum, frame):
            raise Cancelled()
        signal.signal(signal.SIGTERM, cancel)
    try:
        # GUI attaches the Windows Job Object before sending this command.
        request = json.loads(sys.stdin.readline())
        return run_inspection(request) if request.get("action") == "inspect" else run_download(request)
    except Cancelled:
        return 130
    except (DependencyError, OSError, ValueError, KeyError, RuntimeError) as error:
        emit({"type": "error", "message": str(error)})
        return 1
    except Exception as error:
        emit({"type": "error", "message": f"{type(error).__name__}: {error}"})
        return 1
