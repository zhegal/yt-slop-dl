import hashlib
import io
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QLockFile

from app import dependencies as deps
from app import updates


VERSION = '2099.01.02'
PAYLOAD = b'new verified yt-dlp'


def install_fixture(folder, files, version='2099.01.01'):
    folder.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, payload in files.items():
        path = folder / name
        path.write_bytes(payload)
        path.chmod(0o755)
        hashes[name] = hashlib.sha256(payload).hexdigest()
    updates.write_state(folder / 'installed.json', {'files': hashes, 'version': version})


@pytest.fixture
def update_setup(tmp_path, monkeypatch):
    platform = deps.Platform('macos', 'arm64')
    monkeypatch.setattr(updates, 'detect_platform', lambda: platform)
    monkeypatch.setattr(deps, 'detect_platform', lambda: platform)
    folder = tmp_path / platform.key
    active = folder / 'yt-dlp'
    install_fixture(active, {'yt-dlp': b'old working yt-dlp'})
    target = {'version': VERSION, 'name': 'yt-dlp_macos',
              'url': f'https://github.com/yt-dlp/yt-dlp/releases/download/{VERSION}/yt-dlp_macos',
              'sha256': hashlib.sha256(PAYLOAD).hexdigest()}
    release = {'tag_name': VERSION, 'assets': [{'name': target['name'], 'browser_download_url': target['url'],
                                              'digest': 'sha256:' + target['sha256']}]}
    calls = []
    def fetch(asset, binary, digest, report):
        calls.append('fetch')
        binary.write_bytes(PAYLOAD)
        deps.verify_checksum(binary, digest)
        report(100)
    def probe(args, **kwargs):
        calls.append('probe')
        assert args[1:] == ['--version'] and not kwargs.get('shell')
        kwargs['stdout'].write((VERSION + '\n').encode())
        return SimpleNamespace(stdout=VERSION + '\n')
    monkeypatch.setattr(updates, 'release_info', lambda *args, **kw: release)
    monkeypatch.setattr(updates, 'fetch', fetch)
    monkeypatch.setattr(updates.subprocess, 'run', probe)
    tools = {'yt-dlp': active / 'yt-dlp'}
    return tmp_path, folder, active, target, release, calls, tools


def test_startup_checks_metadata_only_once_per_24h(update_setup, monkeypatch):
    root, folder, active, target, release, calls, tools = update_setup
    queries = []
    monkeypatch.setattr(updates, 'release_info', lambda *args, **kw: queries.append(kw) or release)
    for now in (100000, 100001, 100000 + updates.DAY - 1):
        assert updates.check_for_updates(root, now=now) == {'status': 'available', 'version': VERSION}
    assert len(queries) == 1 and queries[0]['timeout'] == 7
    assert not calls and tools['yt-dlp'].read_bytes() == b'old working yt-dlp'
    updates.check_for_updates(root, now=100000 + updates.DAY)
    assert len(queries) == 2
    # Clock changes must not suppress checking indefinitely.
    updates.check_for_updates(root, now=1)
    assert len(queries) == 3


def test_failed_check_is_throttled_and_keeps_previous_target(update_setup, monkeypatch):
    root, folder, active, target, release, calls, tools = update_setup
    updates.check_for_updates(root, now=100000)
    queries = []
    def offline(*args, **kwargs):
        queries.append(1)
        raise OSError('offline')
    monkeypatch.setattr(updates, 'release_info', offline)
    assert updates.check_for_updates(root, now=200000)['status'] == 'check_failed'
    assert updates.check_for_updates(root, now=200001)['status'] == 'check_failed'
    assert len(queries) == 1
    assert updates.read_state(folder / 'yt-dlp-update.json')['target'] == target
    assert tools['yt-dlp'].read_bytes() == b'old working yt-dlp'


@pytest.mark.parametrize('change', ['external_url', 'bad_digest', 'prerelease', 'missing_asset'])
def test_untrusted_or_incomplete_release_is_not_scheduled(update_setup, change):
    root, folder, active, target, release, calls, tools = update_setup
    if change == 'external_url':
        release['assets'][0]['browser_download_url'] = 'https://example.test/tool'
    elif change == 'bad_digest':
        release['assets'][0]['digest'] = 'sha256:invalid'
    elif change == 'prerelease':
        release['prerelease'] = True
    else:
        release['assets'] = []
    assert updates.check_for_updates(root, now=100000)['status'] == 'check_failed'
    assert 'target' not in updates.read_state(folder / 'yt-dlp-update.json')
    assert not calls


def test_update_commits_only_after_verification_and_retains_old_version(update_setup):
    root, folder, active, target, release, calls, tools = update_setup
    updates.check_for_updates(root, now=100000)
    events = []
    with updates.operation_lock(root):
        updates.apply_update(root, tools, events.append, {}, now=100001)
    assert calls == ['fetch', 'probe']
    assert tools['yt-dlp'].read_bytes() == PAYLOAD
    assert deps.cached_tools(active, {'yt-dlp'})
    previous = folder / 'yt-dlp.previous'
    assert (previous / 'yt-dlp').read_bytes() == b'old working yt-dlp'
    assert deps.cached_tools(previous, {'yt-dlp'})
    assert updates.read_state(active / 'installed.json')['version'] == VERSION
    assert events[-1]['status'] == 'installed'
    assert not list(folder.glob('.install-*'))
    updates.apply_update(root, tools, events.append, {}, now=100002)
    assert calls == ['fetch', 'probe']
    assert updates.check_for_updates(root, now=100003)['status'] == 'current'


@pytest.mark.parametrize('failure', ['checksum', 'network', 'health', 'wrong_version', 'publish'])
def test_update_failure_preserves_old_tool_and_is_retried_after_a_day(update_setup, monkeypatch, failure):
    root, folder, active, target, release, calls, tools = update_setup
    updates.check_for_updates(root, now=100000)
    if failure in {'checksum', 'network'}:
        def fail(*args, **kw):
            calls.append('failed fetch')
            raise deps.DependencyError('checksum mismatch') if failure == 'checksum' else OSError('offline')
        monkeypatch.setattr(updates, 'fetch', fail)
    elif failure == 'health':
        def fail(*args, **kw):
            raise subprocess.CalledProcessError(1, ['yt-dlp', '--version'])
        monkeypatch.setattr(updates.subprocess, 'run', fail)
    elif failure == 'wrong_version':
        monkeypatch.setattr(updates.subprocess, 'run', lambda *args, **kw: SimpleNamespace(stdout='unexpected'))
    else:
        original_replace = deps.os.replace
        failed_once = []
        def fail(source, destination):
            if Path(destination) == active / 'installed.json' and not failed_once:
                failed_once.append(True)
                raise OSError('interrupted manifest publication')
            return original_replace(source, destination)
        monkeypatch.setattr(deps.os, 'replace', fail)
    events = []
    updates.apply_update(root, tools, events.append, {}, now=100001)
    assert tools['yt-dlp'].read_bytes() == b'old working yt-dlp'
    assert deps.cached_tools(active, {'yt-dlp'}) and events[-1]['status'] == 'failed'
    assert not list(folder.glob('.install-*'))
    count = len(calls)
    updates.apply_update(root, tools, events.append, {}, now=100002)
    assert len(calls) == count
    updates.apply_update(root, tools, events.append, {}, now=100001 + updates.DAY)
    assert len(calls) > count


def test_corrupt_or_interrupted_active_install_restores_backup_offline(update_setup, monkeypatch):
    root, folder, active, target, release, calls, tools = update_setup
    deps.copy_cached_tools(active, folder / 'yt-dlp.previous', {'yt-dlp'})
    tools['yt-dlp'].write_bytes(b'interrupted update')
    install_fixture(folder / 'ffmpeg', {'ffmpeg': b'ffmpeg', 'ffprobe': b'ffprobe'})
    install_fixture(folder / 'deno', {'deno': b'deno'})
    def forbidden(*_):
        pytest.fail('Recovery must not require network access')
    monkeypatch.setattr(deps, 'release_assets', forbidden)
    events = []
    restored = deps.ensure_dependencies(root, events.append)
    assert restored['yt-dlp'].read_bytes() == b'old working yt-dlp'
    assert any(e.get('status') == 'restored' for e in events)


def test_operation_lock_prevents_replacement_while_tool_is_in_use(update_setup):
    root, folder, *_ = update_setup
    other = QLockFile(str(folder / 'yt-dlp-operation.lock'))
    with updates.operation_lock(root):
        assert not other.tryLock(0)
        # Metadata checks don't need the operation lock or replace the executable.
        assert updates.check_for_updates(root, now=100000)['status'] == 'available'
    assert other.tryLock(0)
    other.unlock()


def test_corrupt_update_state_is_ignored(update_setup):
    root, folder, active, target, release, calls, tools = update_setup
    (folder / 'yt-dlp-update.json').write_text('truncated')
    updates.apply_update(root, tools, lambda _: None, {})
    assert not calls and tools['yt-dlp'].read_bytes() == b'old working yt-dlp'
    assert updates.check_for_updates(root, now=100000)['status'] == 'available'


@pytest.mark.parametrize('action', ['inspect', 'download'])
def test_worker_installs_pending_update_before_starting_yt_dlp(tmp_path, monkeypatch, action):
    from app import worker
    order = []
    tools = {name: tmp_path / name for name in ('yt-dlp', 'ffmpeg', 'deno')}
    monkeypatch.setattr(worker, 'emit', lambda _: None)
    monkeypatch.setattr(worker, 'ensure_dependencies', lambda *_: order.append('ensure') or tools)
    monkeypatch.setattr(worker, 'apply_update', lambda *_: order.append('update'))
    def start(*args, **kwargs):
        order.append('start yt-dlp')
        raise RuntimeError('stop before a real download')
    monkeypatch.setattr(worker.subprocess, 'Popen', start)
    request = {'url': 'https://www.youtube.com/watch?v=abcdefghijk', 'downloads': str(tmp_path),
               'staging': str(tmp_path), 'components': str(tmp_path)}
    with pytest.raises(RuntimeError, match='stop before a real download'):
        (worker.run_inspection if action == 'inspect' else worker.run_download)(request)
    assert order == ['ensure', 'update', 'start yt-dlp']


def test_worker_holds_operation_lock_for_the_entire_media_operation(qt_app, tmp_path, monkeypatch):
    from app import worker
    monkeypatch.setattr(worker, 'connect_stdio', lambda: None)
    monkeypatch.setattr(worker.signal, 'signal', lambda *_: None)
    monkeypatch.setattr(worker.sys, 'stdin', io.StringIO(json.dumps({'action': 'download', 'components': str(tmp_path)})))
    calls = []
    def operation(request):
        lock = QLockFile(str(tmp_path / deps.detect_platform().key / 'yt-dlp-operation.lock'))
        assert not lock.tryLock(0)
        calls.append('locked')
        return 0
    monkeypatch.setattr(worker, 'run_download', operation)
    assert worker.main() == 0 and calls == ['locked']
    lock = QLockFile(str(tmp_path / deps.detect_platform().key / 'yt-dlp-operation.lock'))
    assert lock.tryLock(0)
    lock.unlock()


def test_installer_launches_real_private_yt_dlp_and_preserves_backup(tmp_path, monkeypatch):
    from app.worker import tool_environment
    configured = os.environ.get('YT_SLOP_DL_TEST_COMPONENTS')
    if not configured:
        pytest.skip('Private tools not configured')
    components = Path(configured)
    platform = deps.detect_platform()
    source = components / platform.key / 'yt-dlp' / ('yt-dlp' + platform.suffix)
    version = subprocess.run([str(source), '--version'], capture_output=True, text=True, encoding='utf-8',
                             check=True, timeout=30, env=tool_environment(components)).stdout.strip()
    folder = tmp_path / platform.key
    name = 'yt-dlp' + platform.suffix
    active = folder / 'yt-dlp'
    install_fixture(active, {name: b'previous fixture executable'})
    asset_name = next(iter(deps.component_sources(platform)['yt-dlp'][2]))
    target = {'version': version, 'name': asset_name,
              'url': f'https://github.com/yt-dlp/yt-dlp/releases/download/{version}/{asset_name}',
              'sha256': deps.sha256(source)}
    updates.write_state(folder / 'yt-dlp-update.json', {'target': target})
    def cached_download(asset, binary, digest, report):
        shutil.copyfile(source, binary)
        deps.verify_checksum(binary, digest)
        report(100)
    monkeypatch.setattr(updates, 'fetch', cached_download)
    events = []
    with updates.operation_lock(tmp_path):
        updates.apply_update(tmp_path, {'yt-dlp': active / name}, events.append, tool_environment(components))
    assert events[-1]['status'] == 'installed'
    assert deps.sha256(active / name) == target['sha256']
    assert (folder / 'yt-dlp.previous' / name).read_bytes() == b'previous fixture executable'
