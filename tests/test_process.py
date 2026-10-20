import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QProcess, QTimer

import app.downloader as module


FAKE_WORKER = r'''
import json, pathlib, subprocess, sys, time
r = json.loads(sys.stdin.readline())
if r.get('action') == 'inspect':
    print(json.dumps({'type':'metadata','info':{'title':'Test preview'}}), flush=True)
    sys.exit(0)
if 'abcdefghijk' in r['url']:
    print(json.dumps({'type':'error','message':'controlled failure'}), flush=True)
    sys.exit(1)
if '12345678901' in r['url']:
    p = pathlib.Path(r['downloads']) / 'complete.mp4'
    p.write_bytes(b'complete')
    print(json.dumps({'type':'success','path':str(p)}), flush=True)
    sys.exit(0)
child = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])
print(json.dumps({'type':'child_started','pid':child.pid}), flush=True)
time.sleep(60)
'''


def pump_until(app, predicate, seconds=6):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert predicate(), "Timed out waiting for the asynchronous process"


@pytest.fixture
def fake_worker(monkeypatch):
    class FakeWorker(QProcess):
        def setArguments(self, args):
            super().setArguments(["-u", "-c", FAKE_WORKER])
    monkeypatch.setattr(module, "QProcess", FakeWorker)


@pytest.mark.skipif(os.name == "nt", reason="POSIX integration test; Windows Job Object requires Windows")
def test_cancel_descendants_keep_gui_responsive_and_retry(qt_app, tmp_path, fake_worker):
    downloader = module.Downloader()
    results, children, ticks = [], [], []
    downloader.finished.connect(lambda *result: results.append(result))
    def on_event(event):
        if event.get("type") == "child_started":
            children.append(event["pid"])
            # Give the child time to install its SIGTERM handler.
            QTimer.singleShot(150, downloader.cancel)
    downloader.event.connect(on_event)
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        downloader.start("https://youtu.be/dQw4w9WgXcQ", tmp_path, tmp_path / "tools")
        pump_until(qt_app, lambda: len(results) == 1)
        assert results[0][0] == "cancelled"
        assert ticks and len(ticks) > 15
        assert not downloader.busy
        assert unrelated.poll() is None
        assert not list(tmp_path.glob(".media-downloader-*"))
        # A zombie is already dead and can briefly remain until the OS reaps it.
        def child_dead():
            out = subprocess.run(["ps", "-p", str(children[0]), "-o", "stat="], capture_output=True, text=True)
            return out.returncode != 0 or out.stdout.strip().startswith("Z")
        pump_until(qt_app, child_dead)
        downloader.start("https://youtu.be/abcdefghijk", tmp_path, tmp_path / "tools")
        pump_until(qt_app, lambda: len(results) == 2)
        assert results[1] == ("error", "controlled failure")
        assert not downloader.busy
        downloader.start("https://youtu.be/12345678901", tmp_path, tmp_path / "tools")
        pump_until(qt_app, lambda: len(results) == 3)
        assert results[2][0] == "success"
        assert Path(results[2][1]).read_bytes() == b"complete"
        assert not list(tmp_path.glob(".media-downloader-*"))
    finally:
        timer.stop()
        if downloader.busy:
            downloader.cancel()
            pump_until(qt_app, lambda: not downloader.busy)
        unrelated.terminate()
        unrelated.wait(timeout=3)


def test_process_start_failure(qt_app, tmp_path, monkeypatch):
    class MissingWorker(QProcess):
        def setProgram(self, program):
            super().setProgram(str(tmp_path / "does-not-exist"))
    monkeypatch.setattr(module, "QProcess", MissingWorker)
    downloader = module.Downloader()
    results = []
    downloader.finished.connect(lambda *result: results.append(result))
    downloader.start("https://youtu.be/dQw4w9WgXcQ", tmp_path, tmp_path / "tools")
    pump_until(qt_app, lambda: bool(results))
    assert results[0][0] == "error"
    assert not downloader.busy
    assert not list(tmp_path.glob(".media-downloader-*"))


def test_inspection_returns_metadata_without_creating_downloads(qt_app, tmp_path, fake_worker):
    downloader = module.Downloader()
    results, events = [], []
    downloader.finished.connect(lambda *result: results.append(result))
    downloader.event.connect(events.append)
    downloads = tmp_path / 'never-created'
    downloader.start('https://youtu.be/abcdefghijk', downloads, tmp_path / 'tools', action='inspect')
    pump_until(qt_app, lambda: bool(results))
    assert results == [('inspected','')]
    assert events[0]['info']['title'] == 'Test preview'
    assert not downloads.exists()
    assert not downloader.busy
