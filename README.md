# YT Slop DL — MVP 0.1

A minimal native desktop application for downloading one YouTube video with selectable quality and video/audio streams. Video and audio are merged by FFmpeg without re-encoding. The first click inspects the video without downloading media. A preview shows the title, thumbnail, duration, available resolutions and video/audio codecs. Choose a quality, check Video and/or Audio, select a compatible container, then explicitly start the download. Best quality and both streams are selected by default. Auto follows yt-dlp's MP4/MKV compatibility rules; container changes retain the selected encoded streams. Incompatible containers are disabled. Files are saved to the system Downloads folder by default; use **Choose folder…** before downloading to select another destination. The chosen folder is saved in native application settings and restored after restart. If it was renamed, deleted or replaced with a file, the preference is cleared and the destination falls back to system Downloads. The folder is monitored while the app is idle and checked again before downloading; an active operation keeps its original destination until it finishes. Existing files are preserved and name collisions receive a numeric suffix.

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

The interface follows the system light/dark theme. Use the **App language** selector to switch immediately between Русский, Українська and English, including during a download. The selection is saved in native application settings and restored after restart. Until a language is chosen, the system locale is used (English fallback). `python -m app --language ru` (`uk` or `en` also accepted) overrides the saved/system language for that launch without changing the saved preference. Upstream error details retain their original language.

Paste a YouTube watch, short-link or Shorts URL and click **Analyze** to inspect it. Select quality, Video/Audio checkboxes and a compatible container, then click **Download** to save it. Editing the URL invalidates the previous preview. If stream availability changes, inspect the URL again. MP4 may contain AV1/VP9 or Opus, so player support depends on the codecs as well as the container. Progress reports the current stream, percentage, speed and remaining time when available. **Cancel** stops the worker and its descendants; **Open file** appears first after success and opens the downloaded video/audio in its default system app. **Open folder** opens the actual saved file's folder, even if the destination for the next download has changed. Folder selection is disabled during an operation. Closing during a download cancels it before the window closes.

Video quality lists the available resolutions and frame rates; audio-only quality lists source codecs and bitrates in the preferred audio language. Audio-only Auto preserves the source audio without re-encoding (usually M4A for AAC or Opus). Compatible M4A/Opus containers also appear in audio-only mode. Both checkboxes may be cleared, but downloading stays disabled until a stream is selected. If a quality is available only as a combined video/audio file, audio-only extraction is supported; video-only requires a separate video stream and is disabled for that quality.

## YouTube authorization

Click **Authorization…**, choose a signed-in browser/profile or a **Netscape cookies.txt** export, then click **Import session**. yt-dlp reads the browser only during this explicit import. Only YouTube cookies are retained in the app's application-data folder (`authorization/cookies.txt`); analysis and downloads reuse private temporary copies of that saved session. The original export is never modified and can be removed after import. Cookies are not placed in native settings, the bundle or Git. The saved file is not encrypted: access is restricted to the current user (POSIX directory/file modes 0700/0600; a user-only directory ACL on Windows). Temporary browser-wide exports are removed after completion, error or cancellation; a force-quit/OS crash can leave private temporary files.

**Import again** manually replaces the session; failed imports retain the previous one. Selecting **None** and **Disable and delete session** removes the saved cookies. Old browser selections require an explicit first import. Missing/expired local credentials or an explicit YouTube sign-in/cookie rejection produce a reimport notice; that notice survives restarts until reimport/disable. Ordinary network errors and missing channel membership do not mark the session expired. The app never silently reimports. YouTube can rotate/revoke browser sessions, so a saved session cannot be guaranteed to last indefinitely.

This supports account-only, private and members-only videos **when the account has access and yt-dlp supports the content**. It does not unlock subscriptions or DRM. Cookies can expire; browser database locks, OS keychain permissions or browser encryption can prevent extraction. Close the browser or use a fresh file export if extraction fails. Keep exports private: cookies grant account access. See the official [cookie FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp) and [YouTube export instructions](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies), including yt-dlp's account-use limitations. The app does not implement Google password/OAuth login or PO Token setup.

## Private components

The first inspection installs missing tools automatically into the system application-data folder under `YT Slop DL/YT Slop DL/components`. Previously installed components are reused. Global installations are not used. Allow network access to GitHub and YouTube and sufficient space for both temporary streams and the final file.

Sources: official [yt-dlp releases](https://github.com/yt-dlp/yt-dlp/releases) (standalone executables including EJS), [yt-dlp FFmpeg builds](https://github.com/yt-dlp/FFmpeg-Builds/releases) on Windows/Linux, [ffmpeg-static b6.1.1](https://github.com/eugeneware/ffmpeg-static/releases/tag/b6.1.1) on macOS, and official [Deno releases](https://github.com/denoland/deno/releases). Deno is necessary for current YouTube JavaScript challenges; it is not a frontend or web server.

Downloads are checked against publisher SHA-256 digests obtained over HTTPS from the GitHub release API (or release checksum files). Installation fails if a checksum is unavailable or mismatches. This is integrity verification, not independent signature verification. Cached binaries are checked before reuse. Publisher licenses apply to the downloaded components.

On startup the app asynchronously checks the latest **stable yt-dlp** release, at most once per 24 hours across restarts (failed checks count too). Analysis/download buttons wait for this short check; the window remains responsive. An available update is installed before the next analysis or download, with SHA-256 verification and a `--version` launch check. The previous verified executable and manifest are retained in `yt-dlp.previous` next to the active installation. Updates never replace a running downloader: media operations share a process lock across app instances. A failed download, checksum, launch check or installation keeps/restores the installed version, with another update attempt allowed after 24 hours. An interrupted/corrupt active installation can be restored from the backup offline. No manual yt-dlp update commands are needed. YouTube/authentication/network errors alone do not trigger a downgrade; passing the launch check cannot guarantee every extractor still works. FFmpeg/FFprobe and Deno are installed/repaired when needed, without version auto-updates. The app itself is not auto-updated.

## Desktop bundle

```sh
python build.py
```

Run on the target OS with `.[build]` installed. PyInstaller creates an application under `dist/`: a clickable `.app` on macOS, an `.exe` directory on Windows, or a Linux executable with a local `.desktop` launcher. Keep the generated bundle together. Components are installed on first inspection, independently of the bundle. A macOS bundle can be moved to Applications.

macOS builds use the existing stable bundle ID `YT Slop DL` and a real code-signing certificate from Keychain. If exactly one valid identity exists, it is selected automatically. Otherwise run `python build.py --codesign-identity '<certificate SHA-1 or name>'`. A successful build remembers its certificate SHA-1 in Git-ignored `config.local.signing.json`; private keys stay in Keychain. Later builds fail if that identity is unavailable instead of silently falling back to ad-hoc signing. Permissions must be granted once when migrating from the old ad-hoc build, then remain associated with the same signed app across rebuilds. Changing the bundle ID or signing identity can require new permission. `--ad-hoc` explicitly creates a disposable build without this guarantee. Development signing is for local use; notarization, distribution signing and installers remain outside the MVP.

## MVP limits

One non-live video at a time. No playlists, subtitles, history, telemetry or app auto-update system. YouTube can reject requests or require fresh cookies/PO tokens; these restrictions are shown as errors. Network access and GitHub API limits can affect component installation and update checks. Internet-free startup works after Python/Qt are installed; downloading always requires internet access.

Temporary media is isolated in an app-owned hidden directory inside the selected destination and removed after success, error or cancellation. An OS crash/forced termination can leave that directory or an incomplete component-installation directory; these can be removed manually when the app is closed. Cancellation uses a dedicated POSIX process group or a Windows Job Object and never kills processes by executable name.
