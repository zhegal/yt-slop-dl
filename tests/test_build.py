"""Signing selection uses synthetic identities, never the real Keychain."""

import json
from types import SimpleNamespace

import pytest

from build import SIGNING_CONFIG, signing_identity


def test_release_versions_agree():
    import tomllib
    from pathlib import Path
    from app import __version__
    root = Path(__file__).resolve().parents[1]
    assert tomllib.loads((root/'pyproject.toml').read_text())['project']['version'] == __version__ == '0.2.0'
    assert f'#define AppVersion "{__version__}"' in (root/'packaging/windows.iss').read_text()


FIRST = "A" * 40
SECOND = "B" * 40


def keychain(monkeypatch, identities):
    def run(args, **kwargs):
        assert args == ["security", "find-identity", "-v", "-p", "codesigning"]
        return SimpleNamespace(stdout="\n".join(
            f'  {index}) {digest} "{name}"' for index, (digest, name) in enumerate(identities, 1)))
    monkeypatch.setattr("build.subprocess.run", run)


def test_first_build_selects_only_valid_identity(tmp_path, monkeypatch):
    keychain(monkeypatch, [(FIRST, "Apple Development: Test")])
    assert signing_identity(tmp_path) == FIRST


def test_pinned_identity_does_not_change_when_another_is_added(tmp_path, monkeypatch):
    (tmp_path / SIGNING_CONFIG).write_text(json.dumps({"identity": FIRST}))
    keychain(monkeypatch, [(SECOND, "Other"), (FIRST, "Original")])
    assert signing_identity(tmp_path) == FIRST


@pytest.mark.parametrize("identities", [[], [(SECOND, "Replacement")]])
def test_missing_pinned_identity_fails_instead_of_replacing_it(tmp_path, monkeypatch, identities):
    (tmp_path / SIGNING_CONFIG).write_text(json.dumps({"identity": FIRST}))
    keychain(monkeypatch, identities)
    with pytest.raises(RuntimeError, match="unavailable or ambiguous"):
        signing_identity(tmp_path)


def test_ambiguous_first_build_requires_explicit_choice(tmp_path, monkeypatch):
    keychain(monkeypatch, [(FIRST, "First"), (SECOND, "Second")])
    with pytest.raises(RuntimeError, match="unavailable or ambiguous"):
        signing_identity(tmp_path)
    assert signing_identity(tmp_path, "Second") == SECOND


@pytest.mark.parametrize("contents", ["broken json", "[]", '{}', '{"identity": null}'])
def test_invalid_local_config_fails_but_explicit_choice_recovers(tmp_path, monkeypatch, contents):
    (tmp_path / SIGNING_CONFIG).write_text(contents)
    keychain(monkeypatch, [(FIRST, "First")])
    with pytest.raises(RuntimeError, match="Invalid"):
        signing_identity(tmp_path)
    assert signing_identity(tmp_path, FIRST.lower()) == FIRST
