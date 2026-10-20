# YT Slop DL — MVP 0.1

A minimal native desktop application for downloading one YouTube video with selectable quality and video/audio streams. Video and audio are merged by FFmpeg without re-encoding. The first click inspects the video without downloading media. A preview shows the title, thumbnail, duration, available resolutions and video/audio codecs. Choose a quality, check Video and/or Audio, select a compatible container, then explicitly start the download. Best quality and both streams are selected by default. Auto follows yt-dlp's MP4/MKV compatibility rules; container changes retain the selected encoded streams. Incompatible containers are disabled. Files are saved to the system Downloads folder; existing files are preserved and name collisions receive a numeric suffix.

Targets **64-bit Windows, macOS and glibc Linux**, on x64 and ARM64. Built with Python 3.12+, PySide6 (Essentials/Qt Widgets), yt-dlp, FFmpeg/FFprobe and QProcess. Platform support is intended; only macOS ARM64 has been exercised during development.

## Development

```sh
python3 -m venv .venv
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[dev,build]'
python -m app
python -m pytest
```

The optional offline media integration tests use already installed private tools: set `YT_SLOP_DL_TEST_COMPONENTS` to the `components` directory described below. They generate tiny local fixtures and verify MP4/MKV/WebM output, progress and identical encoded packet hashes. They also verify lower quality, video-only/audio-only output, and stream-copy audio extraction. They never access YouTube or install components.

The interface follows the system light/dark theme and uses Russian, Ukrainian or English according to the system locale (English fallback). Override with `python -m app --language ru` (`uk` or `en` also accepted). Upstream error details retain their original language.

Paste a YouTube watch, short-link or Shorts URL and click **Analyze** to inspect it. Select quality, Video/Audio checkboxes and a compatible container, then click **Download** to save it. Editing the URL invalidates the previous preview. If stream availability changes, inspect the URL again. MP4 may contain AV1/VP9 or Opus, so player support depends on the codecs as well as the container. Progress reports the current stream, percentage, speed and remaining time when available. **Cancel** stops the worker and its descendants; **Open folder** appears after success. Closing during a download cancels it before the window closes.

Video quality lists the available resolutions and frame rates; audio-only quality lists source codecs and bitrates in the preferred audio language. Audio-only Auto preserves the source audio without re-encoding (usually M4A for AAC or Opus). Compatible M4A/Opus containers also appear in audio-only mode. Both checkboxes may be cleared, but downloading stays disabled until a stream is selected. If a quality is available only as a combined video/audio file, audio-only extraction is supported; video-only requires a separate video stream and is disabled for that quality.

## Private components

The first inspection installs missing tools automatically into the system application-data folder under `YT Slop DL/YT Slop DL/components`. Existing components from the previous `YT Slop DL/Media Downloader/components` location are reused. Global installations are not used. Allow network access to GitHub and YouTube and sufficient space for both temporary streams and the final file.

Sources: official [yt-dlp releases](https://github.com/yt-dlp/yt-dlp/releases) (standalone executables including EJS), [yt-dlp FFmpeg builds](https://github.com/yt-dlp/FFmpeg-Builds/releases) on Windows/Linux, [ffmpeg-static b6.1.1](https://github.com/eugeneware/ffmpeg-static/releases/tag/b6.1.1) on macOS, and official [Deno releases](https://github.com/denoland/deno/releases). Deno is necessary for current YouTube JavaScript challenges; it is not a frontend or web server.

Downloads are checked against publisher SHA-256 digests obtained over HTTPS from the GitHub release API (or release checksum files). Installation fails if a checksum is unavailable or mismatches. This is integrity verification, not independent signature verification. Cached binaries are checked before reuse. Tools are downloaded only when missing or damaged; no automatic version updates are implemented. Publisher licenses apply to the downloaded components.

## Desktop bundle

```sh
python build.py
```

Run on the target OS with `.[build]` installed. PyInstaller creates an application under `dist/`: a clickable `.app` on macOS, an `.exe` directory on Windows, or a Linux executable with a local `.desktop` launcher. Keep the generated bundle together. Components are installed on first inspection, independently of the bundle. Builds are not signed for distribution; release signing and installers are outside this MVP. A macOS bundle can be moved to Applications.

## MVP limits

One public, non-live video at a time. No playlists, login/cookies, subtitles, history, telemetry or update system. YouTube can reject requests or require authentication/PO tokens; these restrictions are shown as errors. Network access and GitHub API limits can affect initial component installation. Internet-free startup works after Python/Qt are installed; downloading always requires internet access.

Temporary media is isolated in an app-owned hidden directory inside Downloads and removed after success, error or cancellation. An OS crash/forced termination can leave that directory or an incomplete component-installation directory; these can be removed manually when the app is closed. Cancellation uses a dedicated POSIX process group or a Windows Job Object and never kills processes by executable name.
