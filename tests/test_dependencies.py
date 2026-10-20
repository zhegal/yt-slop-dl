import hashlib
import io
import json
import stat
import tarfile
import zipfile

import pytest

import app.dependencies as deps


def test_checksum_and_cached_corruption(tmp_path):
    binary = tmp_path / "ffmpeg"
    binary.write_bytes(b"good tool")
    binary.chmod(0o755)
    digest = hashlib.sha256(b"good tool").hexdigest()
    deps.verify_checksum(binary, digest)
    (tmp_path / "installed.json").write_text(json.dumps({"files": {"ffmpeg": digest}}))
    assert deps.cached_tools(tmp_path, {"ffmpeg"})
    binary.write_bytes(b"tampered")
    assert not deps.cached_tools(tmp_path, {"ffmpeg"})
    with pytest.raises(deps.DependencyError, match="SHA-256"):
        deps.verify_checksum(binary, digest)


def test_archive_copies_only_tools_without_traversal(tmp_path):
    archive = tmp_path / "tools.zip"
    destination = tmp_path / "out"
    destination.mkdir()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../../escaped", "unsafe")
        package.writestr("package/bin/ffmpeg", b"video")
        package.writestr("package/bin/ffprobe", b"probe")
    deps.extract_tools(archive, destination, {"ffmpeg", "ffprobe"})
    assert {p.name for p in destination.iterdir()} == {"ffmpeg", "ffprobe"}
    assert (destination / "ffmpeg").read_bytes() == b"video"
    assert not (tmp_path / "escaped").exists()


def test_archive_rejects_symlink_and_missing_tool(tmp_path):
    archive = tmp_path / "tools.zip"
    with zipfile.ZipFile(archive, "w") as package:
        link = zipfile.ZipInfo("ffmpeg")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        package.writestr(link, "/etc/passwd")
    with pytest.raises(deps.DependencyError, match="all required"):
        deps.extract_tools(archive, tmp_path, {"ffmpeg"})


def test_tar_copies_tools(tmp_path):
    archive = tmp_path / "tools.tar.xz"
    with tarfile.open(archive, "w:xz") as package:
        member = tarfile.TarInfo("package/bin/ffmpeg")
        member.size = 5
        package.addfile(member, io.BytesIO(b"video"))
    deps.extract_tools(archive, tmp_path, {"ffmpeg"})
    assert (tmp_path / "ffmpeg").read_bytes() == b"video"


def test_checksum_required():
    with pytest.raises(deps.DependencyError, match="unavailable"):
        deps.asset_checksum({"name": "tool", "digest": None}, {})


def test_checksum_mismatch_never_installs(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "detect_platform", lambda: deps.Platform("macos", "arm64"))
    asset = {"name": "yt-dlp_macos", "digest": "sha256:" + "0" * 64, "browser_download_url": "https://example.test/tool"}
    monkeypatch.setattr(deps, "release_assets", lambda *_: {"yt-dlp_macos": asset})
    class Response(io.BytesIO):
        headers = {"Content-Length": "3"}
    monkeypatch.setattr(deps, "open_url", lambda _: Response(b"bad"))
    with pytest.raises(deps.DependencyError, match="verification failed"):
        deps.ensure_dependencies(tmp_path)
    assert not list(tmp_path.rglob("installed.json"))
    assert not list(tmp_path.rglob(".install-*"))


def test_install_cache_and_repair(tmp_path, monkeypatch):
    p = deps.Platform("macos", "arm64")
    monkeypatch.setattr(deps, "detect_platform", lambda: p)
    payloads = {}
    for component, (_, _, mapping) in deps.component_sources(p).items():
        for name, output in mapping.items():
            if output == "archive":
                data = io.BytesIO()
                with zipfile.ZipFile(data, "w") as archive:
                    archive.writestr("deno", b"deno tool")
                payloads[name] = data.getvalue()
            else:
                payloads[name] = name.encode()
    calls = []
    def release(repo, tag):
        calls.append(repo)
        return {name: {"name": name, "digest": "sha256:" + hashlib.sha256(data).hexdigest(), "browser_download_url": name} for name, data in payloads.items()}
    monkeypatch.setattr(deps, "release_assets", release)
    class Response(io.BytesIO):
        @property
        def headers(self):
            return {"Content-Length": str(len(self.getvalue()))}
    monkeypatch.setattr(deps, "open_url", lambda name: Response(payloads[name]))
    tools = deps.ensure_dependencies(tmp_path)
    assert set(tools) == {"yt-dlp", "ffmpeg", "ffprobe", "deno"}
    assert all(path.is_file() for path in tools.values())
    assert len(calls) == 3
    assert deps.ensure_dependencies(tmp_path) == tools
    assert len(calls) == 3  # Offline cache reuse; no update query.
    tools["ffprobe"].write_bytes(b"corrupt")
    deps.ensure_dependencies(tmp_path)
    assert len(calls) == 4 and tools["ffprobe"].read_bytes() == b"ffprobe-darwin-arm64"


def test_install_network_error_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "detect_platform", lambda: deps.Platform("linux", "x64"))
    def fail(*_):
        raise OSError("network unavailable")
    monkeypatch.setattr(deps, "release_assets", fail)
    with pytest.raises(deps.DependencyError, match="Cannot install yt-dlp.*network unavailable"):
        deps.ensure_dependencies(tmp_path)
