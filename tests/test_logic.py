import json
from pathlib import Path

import pytest

from app.dependencies import DependencyError, Platform, component_sources, detect_platform
from app.downloader import build_arguments, error_message, parse_output, validate_url
from app.i18n import TEXT, language_for


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    " https://youtu.be/dQw4w9WgXcQ?t=12 ",
    "http://m.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123",
    "https://youtube.com/shorts/dQw4w9WgXcQ",
    "https://youtube.com/embed/dQw4w9WgXcQ/",
    "https://www.youtube.com/live/dQw4w9WgXcQ",
    "https://music.youtube.com/watch?v=dQw4w9WgXcQ&si=tracking",
])
def test_video_urls(url):
    assert validate_url(url) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.mark.parametrize("url", [
    "", "not a URL", "youtube.com/watch?v=dQw4w9WgXcQ", "file:///video.mp4",
    "https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ",
    "https://youtube.com@evil.test/watch?v=dQw4w9WgXcQ",
    "https://user@youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com:443/watch?v=dQw4w9WgXcQ",
    "https://youtube.com:bad/watch?v=dQw4w9WgXcQ",
    "https://youtube.com/playlist?list=PL123", "https://youtube.com/@channel",
    "https://youtu.be/short", "https://youtu.be/dQw4w9WgXcQ/extra",
    "https://youtube.com/shorts/extra/dQw4w9WgXcQ",
    "https://youtube.com/watch?v=dQw4w9WgXcQ&v=abcdefghijk",
    "https://youtu.be/dQw4w9WgXcQ\n--exec=evil",
    "https://youtube.com/watch?v=../../x", "https://[invalid/watch",
])
def test_rejects_invalid_urls(url):
    with pytest.raises(ValueError, match="invalid_url"):
        validate_url(url)


@pytest.mark.parametrize("system,machine,expected", [
    ("Darwin", "arm64", "macos-arm64"), ("Darwin", "x86_64", "macos-x64"),
    ("Windows", "AMD64", "windows-x64"), ("Windows", "ARM64", "windows-arm64"),
    ("Linux", "x86_64", "linux-x64"), ("Linux", "aarch64", "linux-arm64"),
])
def test_platforms(system, machine, expected):
    p = detect_platform(system, machine)
    assert p.key == expected
    sources = component_sources(p)
    assert set(sources) == {"yt-dlp", "ffmpeg", "deno"}
    assert all(repo in {"yt-dlp/yt-dlp", "yt-dlp/FFmpeg-Builds", "eugeneware/ffmpeg-static", "denoland/deno"} for repo, _, _ in sources.values())


@pytest.mark.parametrize("system,machine", [("FreeBSD", "amd64"), ("Linux", "i686"), ("Windows", "x86")])
def test_unsupported_platform(system, machine):
    with pytest.raises(DependencyError, match="Unsupported"):
        detect_platform(system, machine)


def test_cli_arguments(tmp_path):
    tools = {name: tmp_path / "private tools" / name for name in ("yt-dlp", "ffmpeg", "deno")}
    args = build_arguments("https://youtu.be/dQw4w9WgXcQ?list=PL1", tmp_path / "Відео 100%", tools)
    assert args[args.index("--format") + 1] == "bestvideo+bestaudio/best"
    assert args[args.index("--merge-output-format") + 1] == "mp4/mkv"
    assert args[args.index("--remux-video") + 1] == "webm>mkv"
    assert args[args.index("--ffmpeg-location") + 1] == str(tools["ffmpeg"].parent)
    assert args[args.index("--js-runtimes") + 1] == f"deno:{tools['deno']}"
    assert "--no-playlist" in args and "--ignore-config" in args and "--no-plugin-dirs" in args
    assert "--no-overwrites" in args and "--windows-filenames" in args
    assert "--recode-video" not in args and "--restrict-filenames" not in args
    assert args[-2:] == ["--", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"]
    assert "[%(id)s]" in args[args.index("--output") + 1]


def test_output_protocol():
    event = parse_output('MD_PROGRESS{"downloaded":50,"total":100,"speed":20,"eta":2}')
    assert event["type"] == "progress" and event["total"] == 100
    path = "/tmp/Відео [123].mp4"
    assert parse_output("MD_FILE" + json.dumps(path))["path"] == path
    assert parse_output('[Merger] Merging formats into "x.mkv"')["type"] == "processing"
    assert parse_output("MD_STAGEprocessing")["type"] == "processing"
    assert parse_output("MD_PROGRESSbroken")["type"] == "log"
    assert parse_output("MD_PROGRESS[]")["type"] == "log"
    assert parse_output("MD_FILEnull")["type"] == "log"


def test_error_details():
    assert error_message(["INFO: hello", "ERROR: HTTP 403 Forbidden"], 1) == "HTTP 403 Forbidden"
    assert "code 7" in error_message([], 7)
    assert len(error_message(["ERROR: " + "x" * 5000], 1)) <= 3000


def test_translations_complete():
    assert all(set(table) == set(TEXT["en"]) for table in TEXT.values())
    assert language_for("uk_UA") == "uk"
    assert language_for("ru-RU") == "ru"
    assert language_for("de_DE") == "en"
