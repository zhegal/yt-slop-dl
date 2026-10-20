"""Private, checksum-verified tools. No PATH lookup or global installation."""

import hashlib
import json
import os
import platform
import shutil
import stat
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class DependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Platform:
    os: str
    arch: str

    @property
    def key(self) -> str:
        return f"{self.os}-{self.arch}"

    @property
    def suffix(self) -> str:
        return ".exe" if self.os == "windows" else ""


def detect_platform(system: str | None = None, machine: str | None = None) -> Platform:
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    os_name = {"darwin": "macos", "windows": "windows", "linux": "linux"}.get(system)
    arch = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    if not os_name or not arch:
        raise DependencyError(f"Unsupported platform: {system} / {machine}. Use a 64-bit x64 or ARM64 system.")
    if os_name == "linux" and platform.libc_ver()[0] == "musl":
        raise DependencyError("This MVP requires glibc Linux; musl/Alpine builds are not supported.")
    return Platform(os_name, arch)


# Only the projects' own release repositories. macOS FFmpeg is supplied by
# ffmpeg-static, whose release includes matching ffmpeg and ffprobe binaries.
# The latter is pinned because it has no stable latest-release asset naming API.

def component_sources(p: Platform) -> dict[str, tuple[str, str, dict[str, str]]]:
    yt = {
        ("windows", "x64"): "yt-dlp.exe",
        ("windows", "arm64"): "yt-dlp_arm64.exe",
        ("macos", "x64"): "yt-dlp_macos",
        ("macos", "arm64"): "yt-dlp_macos",
        ("linux", "x64"): "yt-dlp_linux",
        ("linux", "arm64"): "yt-dlp_linux_aarch64",
    }[(p.os, p.arch)]
    result = {"yt-dlp": ("yt-dlp/yt-dlp", "latest", {yt: "yt-dlp" + p.suffix})}
    if p.os == "macos":
        result["ffmpeg"] = ("eugeneware/ffmpeg-static", "tags/b6.1.1", {
            f"ffmpeg-darwin-{p.arch}": "ffmpeg",
            f"ffprobe-darwin-{p.arch}": "ffprobe",
        })
    else:
        target = {("windows", "x64"): "win64", ("windows", "arm64"): "winarm64",
                  ("linux", "x64"): "linux64", ("linux", "arm64"): "linuxarm64"}[(p.os, p.arch)]
        ext = "zip" if p.os == "windows" else "tar.xz"
        result["ffmpeg"] = ("yt-dlp/FFmpeg-Builds", "tags/latest", {
            f"ffmpeg-master-latest-{target}-gpl.{ext}": "archive",
        })
    deno_arch = "aarch64" if p.arch == "arm64" else "x86_64"
    deno_os = {"macos": "apple-darwin", "windows": "pc-windows-msvc", "linux": "unknown-linux-gnu"}[p.os]
    result["deno"] = ("denoland/deno", "latest", {f"deno-{deno_arch}-{deno_os}.zip": "archive"})
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_checksum(path: Path, expected: str) -> None:
    if len(expected) != 64 or sha256(path) != expected.lower():
        raise DependencyError(f"SHA-256 verification failed: {path.name}")


def open_url(url: str):
    if not url.startswith("https://"):
        raise DependencyError("Component downloads require HTTPS.")
    req = urllib.request.Request(url, headers={"User-Agent": "YTSlopDL/0.1", "Accept": "application/vnd.github+json"} if url.startswith("https://api.github.com/") else {"User-Agent": "YTSlopDL/0.1"})
    return urllib.request.urlopen(req, timeout=30)


def release_assets(repo: str, release: str) -> dict:
    with open_url(f"https://api.github.com/repos/{repo}/releases/{release}") as response:
        data = json.load(response)
    return {asset["name"]: asset for asset in data["assets"]}


def asset_checksum(asset: dict, assets: dict) -> str:
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:"):
        return digest.removeprefix("sha256:")
    # Older GitHub releases may expose checksum files rather than API digests.
    for name in ("SHA2-256SUMS", "checksums.sha256", asset["name"] + ".sha256sum"):
        if name in assets:
            with open_url(assets[name]["browser_download_url"]) as response:
                lines = response.read(1024 * 1024).decode("utf-8").splitlines()
            for line in lines:
                fields = line.split()
                if len(fields) >= 2 and fields[-1].lstrip("*") == asset["name"]:
                    return fields[0]
    raise DependencyError(f"Publisher SHA-256 is unavailable for {asset['name']}; installation stopped.")


def fetch(asset: dict, target: Path, expected: str, report: Callable) -> None:
    with open_url(asset["browser_download_url"]) as response, target.open("wb") as output:
        total = int(response.headers.get("Content-Length", 0))
        downloaded, previous = 0, -1
        while chunk := response.read(1024 * 1024):
            downloaded += len(chunk)
            if downloaded > 512 * 1024 * 1024:
                raise DependencyError("Component download exceeds the size limit.")
            output.write(chunk)
            percent = int(downloaded * 100 / total) if total else None
            if percent != previous:
                report(percent)
                previous = percent
    verify_checksum(target, expected)


def extract_tools(archive: Path, destination: Path, names: set[str]) -> None:
    """Copy only regular tool files to explicit paths; never extract paths/symlinks."""
    found = set()

    def copy(name, size, stream):
        if name in found or size > 256 * 1024 * 1024:
            raise DependencyError(f"Invalid archive member: {name}")
        with stream, (destination / name).open("wb") as output:
            shutil.copyfileobj(stream, output)
        found.add(name)

    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                name = Path(member.filename).name
                mode = member.external_attr >> 16
                if name in names and not member.is_dir() and not stat.S_ISLNK(mode):
                    copy(name, member.file_size, package.open(member))
    else:
        with tarfile.open(archive, "r:*") as package:
            for member in package:
                name = Path(member.name).name
                if name in names and member.isfile():
                    copy(name, member.size, package.extractfile(member))
    if found != names:
        raise DependencyError("Archive does not contain all required tools.")


def cached_tools(folder: Path, names: set[str]) -> bool:
    try:
        manifest = json.loads((folder / "installed.json").read_text("utf-8"))
        return all(
            (folder / name).is_file()
            and os.access(folder / name, os.X_OK)
            and sha256(folder / name) == manifest["files"][name]
            for name in names
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def ensure_dependencies(root: Path, report: Callable = lambda event: None) -> dict[str, Path]:
    p = detect_platform()
    root = root / p.key
    root.mkdir(parents=True, exist_ok=True)
    tools = {}
    for component, (repo, release, downloads) in component_sources(p).items():
        names = {"ffmpeg" + p.suffix, "ffprobe" + p.suffix} if component == "ffmpeg" else {component + p.suffix}
        folder = root / component
        report({"type": "dependency", "component": component, "percent": None})
        if not cached_tools(folder, names):
            try:
                assets = release_assets(repo, release)
                with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as temporary:
                    stage = Path(temporary)
                    for asset_name, output_name in downloads.items():
                        if asset_name not in assets:
                            raise DependencyError(f"Release asset unavailable: {asset_name}")
                        asset = assets[asset_name]
                        expected = asset_checksum(asset, assets)
                        archive = stage / asset_name
                        fetch(asset, archive, expected, lambda percent: report({"type": "dependency", "component": component, "percent": percent}))
                        if output_name == "archive":
                            extract_tools(archive, stage, names)
                        elif archive.name != output_name:
                            archive.rename(stage / output_name)
                    manifest = {"repository": repo, "assets": list(downloads), "files": {}}
                    for name in names:
                        (stage / name).chmod(0o755)
                        manifest["files"][name] = sha256(stage / name)
                    (stage / "installed.json").write_text(json.dumps(manifest), encoding="utf-8")
                    folder.mkdir(exist_ok=True)
                    # Publish the manifest last, so interrupted installations are retried.
                    for name in sorted(names) + ["installed.json"]:
                        os.replace(stage / name, folder / name)
            except DependencyError:
                raise
            except (OSError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile) as error:
                raise DependencyError(f"Cannot install {component}: {error}") from error
        for name in names:
            tools[name.removesuffix(".exe")] = folder / name
    return tools
