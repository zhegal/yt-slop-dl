import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QColor, QPalette

from app.i18n import translate
from app.window import Window


@pytest.mark.parametrize("language", ["ru", "uk", "en"])
def test_window_launch_and_validation(qt_app, language):
    window = Window(language)
    window.show()
    qt_app.processEvents()
    try:
        assert window.isVisible()
        assert window.windowTitle() == "YT Slop DL"
        assert window.download.text() == translate(language, "analysis")
        window.url.setText("https://invalid.example/video")
        window.download.click()
        assert window.status.text() == translate(language, "invalid_url")
        assert not window.downloader.busy
        assert not window.open_folder.isVisible()
        window._finished("error", "controlled failure")
        assert "controlled failure" in window.detail.text()
        window._finished("success", "/tmp/Відео.mp4")
        assert window.open_folder.isVisible()
        assert window.detail.text() == "Відео.mp4"
        window._finished("cancelled", "")
    finally:
        window.close()


def test_progress_unknown_totals_and_theme(qt_app):
    window = Window("en")
    window._event({"type": "progress", "downloaded": 1024, "total": "NA", "speed": None, "eta": "NA", "format": "v"})
    assert window.progress.maximum() == 0
    window._event({"type": "progress", "downloaded": 500, "total": 1000, "speed": 1024, "eta": 62, "format": "v"})
    assert window.progress.value() == 500
    assert "50.0%" in window.status.text()
    assert "01:02" in window.detail.text() and "1.0 KiB/s" in window.detail.text()
    window._event({"type": "processing"})
    assert "without re-encoding" in window.status.text()
    palette = window.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#222222"))
    window.setPalette(palette)
    window._theme()
    assert "#40444d" in window.styleSheet()
    window.close()


def test_cli_import_and_help():
    result = subprocess.run([sys.executable, "-m", "app", "--help"], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0
    assert "--language" in result.stdout


def test_inspect_then_explicit_download_and_invalidate_preview(qt_app, monkeypatch):
    window = Window('ru')
    window.show()
    calls = []
    monkeypatch.setattr(window.downloader, 'start', lambda *args, **kw: calls.append((args,kw)))
    window.url.setText('https://youtu.be/abcdefghijk')
    window.download.click()
    assert len(calls) == 1 and calls[0][1] == {'action':'inspect'}
    assert not window.preview.isVisible()
    info = {'url':'https://www.youtube.com/watch?v=abcdefghijk','title':'Відео','duration':61,
            'height':1080,'vcodec':'h264','acodec':'aac','format_id':'high+audio',
            'containers':['mkv','mp4'],'resolutions':[360,1080],'thumbnail':None,
            'video_options':[{'format_id':'high','height':1080,'vcodec':'h264','acodec':'none'},
                             {'format_id':'low','height':360,'vcodec':'h264','acodec':'none'}],
            'audio_options':[{'format_id':'audio','vcodec':'none','acodec':'aac','abr':128}]}
    window._event({'type':'metadata','info':info})
    window._finished('inspected','')
    assert len(calls) == 1  # Receiving metadata must not start a download.
    assert window.preview.isVisible() and window.confirm.isEnabled()
    assert window.video_title.text() == 'Відео'
    assert '1:01' in window.video_meta.text()
    assert '1080p' in window.quality.currentText()
    assert not window.container.model().item(window.container.findData('webm')).isEnabled()
    window.container.setCurrentIndex(window.container.findData('mp4'))
    window.confirm.click()
    assert calls[1][1] == {'container':'mp4','format_id':'high+audio','video':True,'audio':True}
    window.url.setText('https://youtu.be/12345678901')
    assert window._info is None and not window.preview.isVisible()
    window._confirm_download()
    assert len(calls) == 2
    window.close()


def test_quality_and_video_audio_checkboxes(qt_app, monkeypatch):
    from test_media import selectable_info
    window = Window('ru')
    window.show()
    calls = []
    monkeypatch.setattr(window.downloader,'start',lambda *args,**kw:calls.append(kw))
    info = selectable_info() | {'url':'https://www.youtube.com/watch?v=abcdefghijk'}
    window.url.setText(info['url'])
    window._show_info(info)
    window._finished('inspected','')
    assert window.download.text() == 'Анализ' and window.confirm.text() == 'Скачать'
    assert window.video_check.isChecked() and window.audio_check.isChecked()
    window.container.setCurrentIndex(window.container.findData('webm'))
    window.quality.setCurrentIndex(window.quality.findData('low'))
    assert not window.container.model().item(window.container.findData('webm')).isEnabled()
    assert window.container.currentData() == 'auto'
    window.audio_check.setChecked(False)
    window.confirm.click()
    assert calls[-1]['format_id'] == 'low' and calls[-1]['video'] and not calls[-1]['audio']
    window.video_check.setChecked(False)
    assert not window.confirm.isEnabled()
    window._busy(False)
    assert not window.confirm.isEnabled()
    window._confirm_download()
    assert len(calls) == 1
    window.audio_check.setChecked(True)
    assert window.confirm.isEnabled()
    assert 'OPUS' in window.quality.currentText()
    window.quality.setCurrentIndex(window.quality.findData('audio-low'))
    assert window.container.model().item(window.container.findData('m4a')).isEnabled()
    window.confirm.click()
    assert calls[-1]['format_id'] == 'audio-low' and not calls[-1]['video'] and calls[-1]['audio']
    window.video_check.setChecked(True)
    window.audio_check.setChecked(False)
    window.quality.setCurrentIndex(window.quality.findData('combined'))
    assert not window.confirm.isEnabled()
    assert 'отдельного видеопотока' in window.detail.text()
    window._busy(True)
    assert not window.video_check.isEnabled() and not window.quality.isEnabled()
    window._busy(False)
    window.close()
