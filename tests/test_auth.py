"""Synthetic session data only: never access the developer's browser/accounts."""

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from app.auth import auth_arguments, normalize_auth, prepare_auth, reimport_needed, saved_session, store_session
from app.downloader import build_arguments, build_inspection_arguments


COOKIE_DATA = b'# Netscape HTTP Cookie File\n#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t2000000000\tTEST_SESSION\tFAKE_SESSION_VALUE\n'
URL = 'https://www.youtube.com/watch?v=abcdefghijk'
SESSION = COOKIE_DATA.replace(b'TEST_SESSION', b'SID')


def test_import_is_scoped_private_persistent_and_independent_of_source(tmp_path):
    original = tmp_path / 'browser-export.txt'
    original.write_bytes(SESSION + b'.example.org\tTRUE\t/\tTRUE\t2000000000\tSECRET\tUNRELATED\n')
    folder = tmp_path / 'authorization'
    store_session(original, folder)
    original.unlink()
    session = saved_session(folder)
    data = Path(session['path']).read_bytes()
    assert b'FAKE_SESSION_VALUE' in data
    assert b'UNRELATED' not in data and b'example.org' not in data
    assert len(list(folder.iterdir())) == 1
    if os.name != 'nt':
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
        assert stat.S_IMODE(Path(session['path']).stat().st_mode) == 0o600
    assert auth_arguments(session) == ['--cookies', str(folder / 'cookies.txt')]


@pytest.mark.parametrize('data,key', [
    (COOKIE_DATA, 'auth_no_session'),
    (SESSION.replace(b'2000000000', b'1'), 'auth_session_expired'),
    (b'broken FAKE_SESSION_VALUE', 'auth_file_invalid'),
    (SESSION.replace(b'.youtube.com', b'.youtube.com.evil.test'), 'auth_file_invalid'),
])
def test_failed_reimport_preserves_existing_session(tmp_path, data, key, capsys):
    original = tmp_path / 'export.txt'
    original.write_bytes(SESSION)
    folder = tmp_path / 'authorization'
    store_session(original, folder)
    previous = (folder / 'cookies.txt').read_bytes()
    original.write_bytes(data)
    with pytest.raises(ValueError, match=f'^{key}$'):
        store_session(original, folder)
    assert (folder / 'cookies.txt').read_bytes() == previous
    assert len(list(folder.iterdir())) == 1
    assert 'FAKE_SESSION_VALUE' not in capsys.readouterr().err


def test_missing_and_expired_saved_session_require_manual_import(tmp_path):
    with pytest.raises(ValueError, match='auth_import_required'):
        saved_session(tmp_path)
    path = tmp_path / 'cookies.txt'
    path.write_bytes(SESSION.replace(b'2000000000', b'1'))
    with pytest.raises(ValueError, match='auth_session_expired'):
        saved_session(tmp_path)


def test_session_cookies_without_expiry_are_preserved(tmp_path):
    path = tmp_path / 'export.txt'
    path.write_bytes(SESSION.replace(b'2000000000', b'0'))
    store_session(path, tmp_path / 'authorization')
    assert saved_session(tmp_path / 'authorization')['source'] == 'file'


@pytest.mark.parametrize('message,expected', [
    ('The provided YouTube account cookies are no longer valid', True),
    ('Sign in to confirm you’re not a bot', True),
    ('Session expired', True),
    ('HTTP Error 403: Forbidden', False),
    ('Network is unreachable', False),
    ('This video is only available to members of this channel', False),
])
def test_reimport_prompt_does_not_misclassify_network_or_membership(message, expected):
    assert reimport_needed(message) == expected


@pytest.mark.parametrize('fails', [False, True])
def test_browser_import_uses_offline_manifest_and_never_exposes_upstream_secrets(tmp_path, monkeypatch, fails):
    from app import worker
    from types import SimpleNamespace
    tools = {name: tmp_path / name for name in ('yt-dlp', 'ffmpeg', 'deno')}
    monkeypatch.setattr(worker, 'ensure_dependencies', lambda *_: tools)
    monkeypatch.setattr(worker, 'apply_update', lambda *_: None)
    events = []
    monkeypatch.setattr(worker, 'emit', events.append)
    real_run = worker.subprocess.run
    def run(args, **kwargs):
        if args[0] in ('whoami', 'icacls'):
            return real_run(args, **kwargs)
        assert args[args.index('--cookies-from-browser') + 1] == 'chrome:Default'
        assert not kwargs.get('shell')
        manifest = json.loads(Path(args[args.index('--load-info-json') + 1]).read_text())
        assert manifest['url'] == 'https://example.invalid/session-import.mp4'
        assert '--simulate' in args and '--skip-download' in args
        Path(args[args.index('--cookies') + 1]).write_bytes(SESSION)
        return SimpleNamespace(returncode=int(fails), stderr=b'FAKE_SESSION_VALUE')
    monkeypatch.setattr(worker.subprocess, 'run', run)
    request = {'auth': {'source': 'browser', 'browser': 'chrome', 'profile': 'Default'},
               'components': str(tmp_path), 'session_folder': str(tmp_path / 'saved'),
               'auth_folder': str(tmp_path / 'work')}
    if fails:
        with pytest.raises(ValueError, match='^auth_import_failed$'):
            worker.run_auth_import(request)
        assert not (tmp_path / 'saved/cookies.txt').exists()
    else:
        assert worker.run_auth_import(request) == 0
        assert saved_session(tmp_path / 'saved')['source'] == 'file'
        assert events[-1] == {'type': 'auth_imported'}
    assert not list((tmp_path / 'work').iterdir())


@pytest.mark.parametrize('auth,expected', [
    (None, []), ({}, []),
    ({'source': 'browser', 'browser': 'firefox'}, ['--cookies-from-browser', 'firefox']),
    ({'source': 'browser', 'browser': 'chrome', 'profile': 'Profile 2'}, ['--cookies-from-browser', 'chrome:Profile 2']),
    ({'source': 'browser', 'browser': 'edge', 'profile': r'C:\Users\Test User\Profile'},
     ['--cookies-from-browser', r'edge:C:\Users\Test User\Profile']),
])
def test_authentication_in_both_commands(tmp_path, auth, expected):
    tools = {name: tmp_path / name for name in ('ffmpeg', 'deno')}
    assert auth_arguments(auth) == expected
    for args in (build_arguments(URL, tmp_path, tools, auth=auth), build_inspection_arguments(URL, tools, auth=auth)):
        assert args[-2:] == ['--', URL]
        if expected:
            index = args.index(expected[0])
            assert args[index:index + 2] == expected
        else:
            assert '--cookies' not in args and '--cookies-from-browser' not in args


@pytest.mark.parametrize('auth', [
    'chrome', [], {'source': 'password'}, {'source': 'browser', 'browser': '--exec'},
    {'source': 'browser', 'browser': 'chrome', 'profile': 'x\n--exec'},
    {'source': 'browser', 'browser': 'firefox', 'profile': 'Default::container'},
    {'source': 'file', 'path': 'relative.txt'}, {'source': 'file', 'path': '/tmp/x\x00'},
])
def test_reject_invalid_authentication(auth):
    with pytest.raises(ValueError, match='^auth_invalid$'):
        normalize_auth(auth)


def test_cookie_snapshot_is_private_and_original_unchanged(tmp_path):
    original = tmp_path / 'Сесія.txt'
    original.write_bytes(COOKIE_DATA)
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    auth = prepare_auth({'source': 'file', 'path': str(original)}, private)
    snapshot = Path(auth['path'])
    assert snapshot != original and snapshot.read_bytes() == COOKIE_DATA
    if os.name != 'nt':
        assert stat.S_IMODE(snapshot.stat().st_mode) == 0o600
    assert auth_arguments(auth) == ['--cookies', str(snapshot)]
    snapshot.write_bytes(b'updated by yt-dlp')
    assert original.read_bytes() == COOKIE_DATA


@pytest.mark.parametrize('data', [
    b'[{"value":"FAKE_SESSION_VALUE"}]',
    b'# Netscape HTTP Cookie File\nmalformed\tFAKE_SESSION_VALUE\n',
    COOKIE_DATA.replace(b'.youtube.com', b'.example.com'),
    b'# Netscape HTTP Cookie File\n',
    b'# Netscape HTTP Cookie File\n\xff\n',
])
def test_invalid_cookie_file_does_not_disclose_contents(tmp_path, data, capsys):
    original = tmp_path / 'input.txt'
    original.write_bytes(data)
    private = tmp_path / 'private'
    private.mkdir()
    with pytest.raises(ValueError, match='^auth_file_invalid$'):
        prepare_auth({'source': 'file', 'path': str(original)}, private)
    assert 'FAKE_SESSION_VALUE' not in capsys.readouterr().err
    assert not list(private.iterdir())
    assert original.read_bytes() == data


def test_missing_cookie_file_and_size_limit(tmp_path, monkeypatch):
    original = tmp_path / 'missing.txt'
    auth = {'source': 'file', 'path': str(original)}
    with pytest.raises(ValueError, match='auth_file_unreadable'):
        prepare_auth(auth, tmp_path)
    original.write_bytes(COOKIE_DATA)
    monkeypatch.setattr('app.auth.MAX_COOKIE_BYTES', 10)
    with pytest.raises(ValueError, match='auth_file_invalid'):
        prepare_auth(auth, tmp_path)


@pytest.mark.parametrize('action', ['inspect', 'download'])
@pytest.mark.parametrize('source', ['browser', 'file'])
@pytest.mark.parametrize('fail', [False, True])
def test_worker_uses_auth_for_both_actions_and_reports_failures(tmp_path, monkeypatch, action, source, fail):
    from app import worker
    tools = {name: tmp_path / name for name in ('yt-dlp', 'ffmpeg', 'deno')}
    monkeypatch.setattr(worker, 'ensure_dependencies', lambda *_: tools)
    monkeypatch.setattr(worker, 'fetch_thumbnail', lambda _: None)
    monkeypatch.setattr(worker, 'summarize_info', lambda info: info)
    monkeypatch.setattr(worker, 'emit', lambda _: None)
    private = tmp_path / 'private'
    private.mkdir()
    original = tmp_path / 'input.txt'
    original.write_bytes(COOKIE_DATA)
    auth = {'source': 'file', 'path': str(original)} if source == 'file' else {'source': 'browser', 'browser': 'firefox'}
    staging = tmp_path / 'staging'
    staging.mkdir()
    video = staging / 'video.mp4'
    video.write_bytes(b'fixture')
    calls = []
    class FakeProcess:
        def __init__(self, args, **kwargs):
            calls.append(args)
            assert not kwargs.get('shell')
            if source == 'file':
                snapshot = Path(args[args.index('--cookies') + 1])
                assert snapshot.parent == private and snapshot != original
                assert snapshot.read_bytes() == COOKIE_DATA
                snapshot.write_bytes(b'yt-dlp may update its jar')
            else:
                assert args[args.index('--cookies-from-browser') + 1] == 'firefox'
            self.stdout = ['ERROR: Session expired\n'] if fail else (
                ['{"title":"Test"}\n'] if action == 'inspect' else ['MD_FILE' + json.dumps(str(video)) + '\n'])
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def wait(self):
            return int(fail)
    monkeypatch.setattr(worker.subprocess, 'Popen', FakeProcess)
    request = {'url': URL, 'downloads': str(tmp_path), 'components': str(tmp_path),
               'staging': str(staging), 'auth': auth, 'auth_folder': str(private)}
    run = worker.run_inspection if action == 'inspect' else worker.run_download
    if fail:
        with pytest.raises(RuntimeError, match='Session expired'):
            run(request)
    else:
        assert run(request) == 0
    assert len(calls) == 1
    assert original.read_bytes() == COOKIE_DATA


def test_private_yt_dlp_loads_cookie_snapshot_offline(tmp_path):
    from app.dependencies import detect_platform
    from app.worker import tool_environment
    configured = os.environ.get('YT_SLOP_DL_TEST_COMPONENTS')
    if not configured:
        pytest.skip('Private tools not configured')
    root = Path(configured) / detect_platform().key
    suffix = '.exe' if os.name == 'nt' else ''
    tools = {name: root / name / (name + suffix) for name in ('yt-dlp', 'ffmpeg', 'deno')}
    original = tmp_path / 'input.txt'
    original.write_bytes(COOKIE_DATA)
    private = tmp_path / 'private'
    private.mkdir()
    auth = prepare_auth({'source': 'file', 'path': str(original)}, private)
    media = tmp_path / 'video.mp4'
    media.write_bytes(b'not read during simulation')
    info = tmp_path / 'info.json'
    info.write_text(json.dumps({'id': 'abcdefghijk', 'title': 'Offline fixture', 'extractor': 'generic',
                               'webpage_url': URL, 'url': media.as_uri(), 'ext': 'mp4',
                               'vcodec': 'avc1', 'acodec': 'mp4a', 'duration': 1}), encoding='utf-8')
    args = build_inspection_arguments(URL, tools, auth=auth)
    result = subprocess.run([str(tools['yt-dlp']), *args[:-2], '--enable-file-urls', '--load-info-json', str(info)],
                            capture_output=True, text=True, encoding='utf-8', timeout=45,
                            env=tool_environment(Path(configured)))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['title'] == 'Offline fixture'
    assert original.read_bytes() == COOKIE_DATA
