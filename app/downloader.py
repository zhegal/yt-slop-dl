"""URL/CLI helpers and a small asynchronous QProcess controller."""

import json
import os
import re
import signal
import shlex
import shutil
import sys
import tempfile
from collections import deque
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.auth import auth_arguments, normalize_auth


VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")


def validate_url(value: str) -> str:
    """Accept a single YouTube video, discard playlist/tracking parameters."""
    value = value.strip()
    if any(ord(c) < 32 for c in value):
        raise ValueError("invalid_url")
    try:
        url = urlsplit(value)
        if url.scheme not in {"https", "http"} or url.username or url.password or url.port:
            raise ValueError("invalid_url")
        host, path = (url.hostname or "").lower(), url.path.rstrip("/")
        if host == "youtu.be":
            video_id = path.removeprefix("/")
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            if path == "/watch":
                ids = parse_qs(url.query).get("v", [])
                video_id = ids[0] if len(ids) == 1 else ""
            elif path.startswith(("/shorts/", "/embed/", "/live/")):
                video_id = path.split("/")[-1] if len(path.split("/")) == 3 else ""
            else:
                video_id = ""
        else:
            video_id = ""
    except ValueError:
        raise ValueError("invalid_url") from None
    if not VIDEO_ID.fullmatch(video_id):
        raise ValueError("invalid_url")
    return f"https://www.youtube.com/watch?v={video_id}"


def build_inspection_arguments(url: str, tools: dict[str, Path], *, auth=None) -> list[str]:
    return [
        "--ignore-config", "--no-plugin-dirs", "--no-remote-components", "--no-playlist",
        "--skip-download", "--simulate", "--dump-single-json", "--no-colors",
        "--encoding", "utf-8", "--socket-timeout", "20", "--retries", "2",
        "--format", "bestvideo+bestaudio/best", "--merge-output-format", "mp4/mkv",
        "--ffmpeg-location", str(tools["ffmpeg"].parent),
        "--no-js-runtimes", "--js-runtimes", f"deno:{tools['deno']}",
        *auth_arguments(auth),
        "--", validate_url(url),
    ]


def build_arguments(url: str, folder: Path, tools: dict[str, Path], container: str = "auto", format_id: str | None = None, *, video=True, audio=True, auth=None, audio_tracks=None) -> list[str]:
    if not video and not audio:
        raise ValueError("empty_selection")
    if container not in ({"auto", "mp4", "mkv", "webm"} if video else {"auto", "mp4", "mkv", "webm", "m4a", "opus"}):
        raise ValueError("Unsupported container")
    if format_id is not None and not re.fullmatch(r"[A-Za-z0-9_.-]+(?:\+[A-Za-z0-9_.-]+)*", format_id):
        raise ValueError("Invalid format selection")
    progress = 'MD_PROGRESS{"downloaded":%(progress.downloaded_bytes)j,"total":%(progress.total_bytes,progress.total_bytes_estimate)j,"speed":%(progress.speed)j,"eta":%(progress.eta)j,"status":%(progress.status)j,"format":%(info.format_id)j}'
    selector = "bestvideo+bestaudio/best" if video and audio else "bestvideo" if video else "bestaudio/best"
    multiple = audio and audio_tracks is not None and len(audio_tracks) > 1
    if multiple and (not format_id or container in {"m4a", "opus"}):
        raise ValueError("Invalid multitrack selection")
    audio_args = ["--extract-audio", "--audio-format", "best"] if not video and not multiple else []
    metadata_args = []
    if multiple:
        flags = []
        for index, track in enumerate(audio_tracks):
            title = str(track.get("title") or track.get("language") or "und")[:300].replace("\0", "").replace("\n", " ").replace("\r", " ")
            flags += [f"-disposition:a:{index}", "default" if index == 0 else "0",
                      f"-metadata:s:a:{index}", f"title={title}", f"-metadata:s:a:{index}", f"handler_name={title}"]
        metadata_args = ["--audio-multistreams", "--embed-metadata", "--no-embed-info-json", "--no-embed-chapters",
                         "--postprocessor-args", "Metadata+ffmpeg_o:" + shlex.join(flags)]
    output_container = "mkv" if multiple and container == "auto" else container
    return [
        "--ignore-config", "--no-plugin-dirs", "--no-remote-components",
        "--no-playlist", "--no-simulate", "--no-overwrites", "--no-continue",
        "--no-colors", "--newline", "--progress", "--progress-delta", "0.3",
        "--encoding", "utf-8", "--socket-timeout", "20", "--retries", "3",
        "--output-na-placeholder", "null",
        "--fragment-retries", "3", "--abort-on-unavailable-fragments",
        "--format", format_id or selector,
        "--merge-output-format", output_container if output_container in {"mp4", "mkv", "webm"} else "mp4/mkv",
        # A combined fallback can be WebM. Remux that container to MKV with -c copy.
        "--remux-video", "webm>mkv" if output_container == "auto" else output_container,
        "--match-filters", "!is_live & !is_upcoming",
        "--ffmpeg-location", str(tools["ffmpeg"].parent),
        "--no-js-runtimes", "--js-runtimes", f"deno:{tools['deno']}",
        "--windows-filenames", "--output", "%(title).150B [%(id)s].%(ext)s",
        "--paths", str(folder),
        "--progress-template", "download:" + progress,
        "--print", "post_process:MD_STAGE%(id)s",
        "--print", "after_move:MD_FILE%(filepath)j",
        *audio_args,
        *metadata_args,
        *auth_arguments(auth),
        "--", validate_url(url),
    ]


def parse_output(line: str) -> dict:
    if line.startswith("MD_PROGRESS"):
        try:
            event = json.loads(line.removeprefix("MD_PROGRESS"))
            if isinstance(event, dict):
                return {**event, "type": "progress"}
        except ValueError:
            pass
    if line.startswith("MD_FILE"):
        try:
            value = json.loads(line.removeprefix("MD_FILE"))
            if isinstance(value, str):
                return {"type": "file", "path": value}
        except ValueError:
            pass
    if line.startswith("MD_STAGE") or line.startswith(("[Merger]", "[VideoRemuxer]", "[Fixup")):
        return {"type": "processing"}
    return {"type": "log", "text": line[-4000:]}


def error_message(lines: list[str], returncode: int) -> str:
    errors = [line.removeprefix("ERROR: ") for line in lines if line.startswith("ERROR:")]
    return ("\n".join(errors[-3:]) or f"yt-dlp exited with code {returncode}.")[-3000:]


# Keep pure logic above importable without a Qt installation.
from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal  # noqa: E402


class Downloader(QObject):
    event = Signal(dict)
    finished = Signal(str, str)  # success/error/cancelled, path/details
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        if os.name != "nt":
            self.process.setUnixProcessParameters(QProcess.UnixProcessFlag.CreateNewSession)
        self.process.started.connect(self._started)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.readyReadStandardError.connect(self._read_error)
        self.process.finished.connect(self._stopped)
        self.process.errorOccurred.connect(self._process_error)
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.setInterval(1200)
        self._kill_timer.timeout.connect(self._force_stop)
        self.busy = False
        self._job = None
        self._pid = 0
        self._cancelled = False
        self._buffer = b""
        self._errors = deque(maxlen=12)
        self._result = None
        self._staging = None
        self._auth_folder = None

    def start(self, url: str, downloads: Path, components: Path, *, action="download", container="auto", format_id=None, video=True, audio=True, auth=None, audio_tracks=None):
        if self.busy:
            return
        url = validate_url(url)
        if action not in {"inspect", "download"}:
            raise ValueError("Unsupported action")
        auth = normalize_auth(auth)
        if action == "download":
            downloads.mkdir(parents=True, exist_ok=True)
            self._staging = Path(tempfile.mkdtemp(prefix=".media-downloader-", dir=downloads))
        try:
            if auth.get("source") == "file":
                self._auth_folder = Path(tempfile.mkdtemp(prefix="yt-slop-dl-auth-"))
        except OSError:
            if self._staging:
                self._cleanup(self._staging)
                self._staging = None
            raise
        self._request = {"url": url, "downloads": str(downloads), "components": str(components),
                         "staging": str(self._staging) if self._staging else None,
                         "auth": auth, "auth_folder": str(self._auth_folder) if self._auth_folder else None,
                         "action": action, "container": container, "format_id": format_id, "video": video, "audio": audio}
        if audio_tracks is not None:
            self._request["audio_tracks"] = audio_tracks
        self._launch()

    def check_updates(self, components: Path):
        if self.busy:
            return
        self._request = {"action": "check_updates", "components": str(components)}
        self._launch()

    def import_auth(self, auth: dict, session_folder: Path, components: Path):
        if self.busy:
            return
        auth = normalize_auth(auth)
        self._auth_folder = Path(tempfile.mkdtemp(prefix="yt-slop-dl-auth-"))
        self._request = {"action": "import_auth", "auth": auth, "auth_folder": str(self._auth_folder),
                         "session_folder": str(session_folder), "components": str(components)}
        self._launch()

    def _launch(self):
        self._cancelled = False
        self._buffer = b""
        self._errors.clear()
        self._result = None
        self._pid = 0
        self.busy = True
        self.busy_changed.emit(True)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUTF8", "1")
        self.process.setProcessEnvironment(env)
        if getattr(sys, "frozen", False):
            self.process.setProgram(sys.executable)
            self.process.setArguments(["--worker"])
        else:
            self.process.setProgram(sys.executable)
            self.process.setArguments(["-u", "-m", "app.main", "--worker"])
            self.process.setWorkingDirectory(str(Path(__file__).resolve().parent.parent))
        self.process.start()

    def _started(self):
        self._pid = self.process.processId()
        try:
            if os.name == "nt":
                from app.process_tree import WindowsJob
                self._job = WindowsJob(self._pid)
            # Worker cannot spawn anything until its process tree is contained.
            if self._cancelled:
                self._terminate()
            else:
                self.process.write((json.dumps(self._request) + "\n").encode("utf-8"))
                self.process.closeWriteChannel()
        except OSError as error:
            self._result = ("error", f"Cannot contain worker process: {error}")
            self.process.kill()

    def _read(self):
        self._buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self._buffer:
            raw, self._buffer = self._buffer.split(b"\n", 1)
            try:
                event = json.loads(raw.decode("utf-8"))
                if not isinstance(event, dict):
                    continue
            except (ValueError, UnicodeError):
                continue
            kind = event.get("type")
            if kind == "success":
                self._result = ("success", event.get("path", ""))
            elif kind == "metadata":
                self._result = ("inspected", "")
                self.event.emit(event)
            elif kind == "update_checked":
                self._result = ("update_checked", "")
                self.event.emit(event)
            elif kind == "auth_imported":
                self._result = ("auth_imported", "")
            elif kind == "error":
                self._result = ("error", event.get("message", "Unknown error"))
            else:
                self.event.emit(event)
        if len(self._buffer) > 4 * 1024 * 1024:
            self._buffer = b""

    def _read_error(self):
        self._errors.append(bytes(self.process.readAllStandardError()).decode("utf-8", "replace")[-3000:])

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._result = ("error", self.process.errorString())
            self._complete()

    def cancel(self):
        if self.busy and not self._cancelled:
            self._cancelled = True
            self._terminate()
            self._kill_timer.start()

    def _signal_group(self, sig):
        if self._pid:
            try:
                os.killpg(self._pid, sig)
            except ProcessLookupError:
                pass
            except PermissionError:
                # macOS can return EPERM for a group containing only dead,
                # reparented children after the worker has already exited.
                if self.process.state() != QProcess.ProcessState.NotRunning:
                    raise

    def _terminate(self):
        if os.name == "nt":
            if self._job:
                self._job.close()  # KILL_ON_JOB_CLOSE includes ffmpeg and Deno.
                self._job = None
            else:
                self.process.kill()  # Worker still waits for the handshake.
        else:
            self._signal_group(signal.SIGTERM)

    def _force_stop(self):
        if os.name != "nt":
            self._signal_group(signal.SIGKILL)
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
        else:
            self._complete()

    def _stopped(self, code, status):
        self._read()
        self._read_error()
        if not self._result and not self._cancelled:
            self._result = ("error", "".join(self._errors).strip() or f"Worker exited with code {code}.")
        if not self._cancelled or not self._kill_timer.isActive():
            self._complete()

    def _complete(self):
        if not self.busy:
            return
        if self._job:
            self._job.close()
            self._job = None
        if os.name != "nt":
            self._signal_group(signal.SIGKILL)  # Also contain unexpected worker failures.
        self._pid = 0
        self._kill_timer.stop()
        if self._staging:
            self._cleanup(self._staging)
            self._staging = None
        if self._auth_folder:
            self._cleanup(self._auth_folder)
            self._auth_folder = None
        self.busy = False
        result = self._result or ("error", "Worker finished without a result.")
        # A file already published successfully must remain visible even if cancel
        # was clicked as the worker was exiting.
        if self._cancelled and result[0] not in {"success", "auth_imported"}:
            result = ("cancelled", "")
        self.busy_changed.emit(False)
        self.finished.emit(*result)

    def _cleanup(self, folder: Path, attempt=0):
        try:
            shutil.rmtree(folder)
        except FileNotFoundError:
            pass
        except OSError:
            # Windows can release file handles just after a job is terminated.
            if attempt < 4:
                QTimer.singleShot(250, lambda: self._cleanup(folder, attempt + 1))
