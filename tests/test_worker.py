import json
import os
import sys
from pathlib import Path

import pytest

from app.worker import publish_file, run_download, tool_environment


def test_publish_preserves_existing_unicode_file(tmp_path):
    source = tmp_path / "staging" / "Відео [123].mkv"
    source.parent.mkdir()
    source.write_bytes(b"new")
    existing = tmp_path / source.name
    existing.write_bytes(b"precious original")
    final = publish_file(source, tmp_path)
    assert final.name == "Відео [123] (1).mkv"
    assert final.read_bytes() == b"new"
    assert existing.read_bytes() == b"precious original"


def test_publish_filesystem_without_hardlinks(tmp_path, monkeypatch):
    source = tmp_path / "staging" / "video.mp4"
    source.parent.mkdir()
    source.write_bytes(b"complete video")
    existing = tmp_path / "video.mp4"
    existing.write_bytes(b"keep me")
    def unsupported(*_):
        raise OSError("hardlinks unsupported")
    monkeypatch.setattr(os, "link", unsupported)
    final = publish_file(source, tmp_path)
    assert final.name == "video (1).mp4"
    assert final.read_bytes() == b"complete video"
    assert existing.read_bytes() == b"keep me"


def test_publish_failure_removes_only_own_reservation(tmp_path, monkeypatch):
    source = tmp_path / "staging" / "video.mp4"
    source.parent.mkdir()
    source.write_bytes(b"new")
    def fail(*_):
        raise OSError("disk error")
    monkeypatch.setattr(os, "link", fail)
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk error"):
        publish_file(source, tmp_path)
    assert not (tmp_path / source.name).exists()
    assert source.read_bytes() == b"new"


def test_external_bundle_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/our/bundle")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/system/path")
    env = tool_environment(tmp_path)
    assert env["LD_LIBRARY_PATH"] == "/system/path"
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert env["DENO_DIR"] == str(tmp_path / "deno-cache")
