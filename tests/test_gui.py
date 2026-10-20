import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QColor, QPalette

from app.i18n import translate
from app.window import Window


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    def create():
        return QSettings(str(tmp_path / 'preferences.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr('app.window.QSettings', create)
    monkeypatch.setattr('app.window.Downloader.check_updates', lambda *_: None)
    original = QStandardPaths.writableLocation
    monkeypatch.setattr(QStandardPaths, 'writableLocation', lambda location:
                        str(tmp_path / 'app-data') if location == QStandardPaths.StandardLocation.AppLocalDataLocation
                        else original(location))
    return create


@pytest.fixture
def fake_session_import(monkeypatch):
    def imported(self, auth, folder, components):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'cookies.txt').write_text('# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2000000000\tSID\tFAKE_SESSION\n')
        self.finished.emit('auth_imported', '')
    monkeypatch.setattr('app.auth_dialog.Downloader.import_auth', imported)


def test_legacy_browser_selection_requires_explicit_import(qt_app, isolated_settings, monkeypatch):
    import json
    preferences = isolated_settings()
    preferences.setValue('authentication', json.dumps({'source': 'browser', 'browser': 'chrome'}))
    window = Window('en')
    calls = []
    monkeypatch.setattr(window.downloader, 'start', lambda *a, **kw: calls.append(kw))
    window.url.setText('https://youtu.be/abcdefghijk')
    window._start()
    assert not calls
    assert 'import' in window.detail.text().lower()
    assert 'Import needed' in window.authentication.text()
    window._finished('error', 'The provided YouTube account cookies are no longer valid')
    assert translate('en', 'auth_reimport_required') in window.detail.text()
    window._finished('error', 'HTTP Error 403: Forbidden')
    assert translate('en', 'auth_reimport_required') not in window.detail.text()
    fresh = Window('en')
    assert fresh._auth_needs_import
    assert fresh.auth_notice.text() == translate('en', 'auth_reimport_required')
    fresh.close()
    window.close()


def test_import_failure_leaves_existing_session_and_dialog_editable(qt_app, tmp_path, monkeypatch):
    from app.auth_dialog import AuthDialog
    window = Window('en')
    window.session_folder.mkdir(parents=True)
    cookie = window.session_folder / 'cookies.txt'
    cookie.write_bytes(b'previous session')
    previous = {'source': 'browser', 'browser': 'chrome', 'profile': 'Default'}
    dialog = AuthDialog('en', previous, window)
    monkeypatch.setattr(dialog.downloader, 'import_auth', lambda *_: dialog.downloader.finished.emit('error', 'auth_import_failed'))
    dialog.browser.setCurrentIndex(dialog.browser.findData('firefox'))
    dialog._apply()
    assert dialog.auth == previous and dialog.result() == 0
    assert cookie.read_bytes() == b'previous session'
    assert dialog.apply_button.isEnabled() and dialog.source.isEnabled()
    assert dialog.error.text() == translate('en', 'auth_import_failed')
    dialog.reject()
    window.close()


def test_cookie_file_import_runs_in_real_worker_without_network(qt_app, tmp_path):
    from PySide6.QtCore import QEventLoop, QTimer
    from app.auth import saved_session
    from app.downloader import Downloader
    original = tmp_path / 'source.txt'
    data = b'# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2000000000\tSID\tFAKE_SESSION\n'
    original.write_bytes(data)
    downloader = Downloader()
    outcomes = []
    loop = QEventLoop()
    downloader.finished.connect(lambda *result: (outcomes.append(result), loop.quit()))
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(downloader.cancel)
    timer.start(10000)
    downloader.import_auth({'source': 'file', 'path': str(original)}, tmp_path / 'saved', tmp_path / 'components')
    loop.exec()
    timer.stop()
    assert outcomes == [('auth_imported', '')]
    assert downloader._auth_folder is None
    assert original.read_bytes() == data
    assert saved_session(tmp_path / 'saved')['source'] == 'file'


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
    assert len(calls) == 1 and calls[0][1] == {'action':'inspect', 'auth':{}}
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
    assert calls[1][1] == {'container':'mp4','format_id':'high+audio','video':True,'audio':True,'auth':{}}
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


def test_choose_destination_cancel_and_open_saved_folder(qt_app, tmp_path, monkeypatch):
    from app.window import QFileDialog, QDesktopServices
    from test_media import selectable_info

    window = Window('ru')
    window.show()
    default = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or Path.home() / 'Downloads')
    assert window.downloads == default
    info = selectable_info() | {'url': 'https://www.youtube.com/watch?v=abcdefghijk'}
    window.url.setText(info['url'])
    window._show_info(info)
    window._finished('inspected', '')
    calls = []
    monkeypatch.setattr(window.downloader, 'start', lambda *args, **kwargs: calls.append(args))
    # Cancelling the native picker must keep the destination and the preview.
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *args: '')
    window.choose_folder.click()
    assert window.downloads == default and window._info == info and not calls
    first = tmp_path / 'Мої відео 100%'
    first.mkdir()
    dialog_arguments = []
    def choose(*args):
        dialog_arguments.append(args)
        return str(first)
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', choose)
    window.choose_folder.click()
    assert dialog_arguments[0][2] == str(default)
    assert str(first) in window.destination.text()
    window.confirm.click()
    assert calls[0][1] == first
    # Both the button and the slot prevent changing an active operation's folder.
    window.downloader.busy = True
    window._busy(True)
    assert not window.choose_folder.isEnabled()
    window._choose_folder()
    assert len(dialog_arguments) == 1
    window.downloader.busy = False
    window._busy(False)
    window._finished('success', str(first / 'video.mp4'))
    second = tmp_path / 'Наступне завантаження'
    second.mkdir()
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *args: str(second))
    window.choose_folder.click()
    assert window.downloads == second
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(url.toLocalFile()) or True)
    window.open_folder.click()
    assert opened == [str(first)]
    window.close()
    # A fresh window and a fresh Python process must both restore the selection.
    fresh = Window('ru')
    assert fresh.downloads == second
    result = subprocess.run([sys.executable, '-c',
        'from PySide6.QtCore import QSettings; import sys; '
        'print(QSettings(sys.argv[1], QSettings.Format.IniFormat).value("download_folder"))',
        fresh._settings.fileName()], capture_output=True, text=True, encoding='utf-8',
        env=os.environ | {'PYTHONIOENCODING': 'utf-8'}, check=True, timeout=10)
    assert result.stdout.strip() == str(second)
    fresh.close()


@pytest.mark.parametrize('change', ['deleted', 'renamed', 'replaced'])
def test_missing_saved_folder_falls_back_and_stays_reset(qt_app, tmp_path, isolated_settings, change):
    saved = tmp_path / 'Saved destination'
    saved.mkdir()
    settings = isolated_settings()
    settings.setValue('download_folder', str(saved))
    settings.sync()
    if change == 'renamed':
        saved.rename(tmp_path / 'Renamed destination')
    else:
        saved.rmdir()
        if change == 'replaced':
            saved.write_text('This is a file, not a folder.')
    window = Window('ru')
    assert window.downloads == window._default_downloads
    assert not isolated_settings().contains('download_folder')
    if saved.is_file():
        saved.unlink()
    saved.mkdir()
    fresh = Window('ru')
    assert fresh.downloads == fresh._default_downloads
    window.close()
    fresh.close()


def test_renamed_folder_resets_while_app_is_open(qt_app, tmp_path, isolated_settings):
    import time
    saved = tmp_path / 'Current destination'
    saved.mkdir()
    settings = isolated_settings()
    settings.setValue('download_folder', str(saved))
    settings.sync()
    window = Window('ru')
    assert window.downloads == saved
    saved.rename(tmp_path / 'Moved destination')
    deadline = time.monotonic() + 3
    while window.downloads == saved and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)
    assert window.downloads == window._default_downloads
    assert str(window._default_downloads) in window.destination.text()
    assert not isolated_settings().contains('download_folder')
    window.close()


def test_deleted_folder_is_not_recreated_when_downloading(qt_app, tmp_path, isolated_settings, monkeypatch):
    from test_media import selectable_info
    saved = tmp_path / 'Old destination'
    saved.mkdir()
    settings = isolated_settings()
    settings.setValue('download_folder', str(saved))
    settings.sync()
    window = Window('ru')
    info = selectable_info() | {'url': 'https://www.youtube.com/watch?v=abcdefghijk'}
    window.url.setText(info['url'])
    window._show_info(info)
    calls = []
    monkeypatch.setattr(window.downloader, 'start', lambda *args, **kwargs: calls.append(args))
    # No event-loop turn: validation before Download must catch the removal too.
    saved.rmdir()
    window._confirm_download()
    assert calls[0][1] == window._default_downloads
    assert not saved.exists()
    assert not isolated_settings().contains('download_folder')
    window.close()


@pytest.mark.parametrize('language', ['ru', 'uk', 'en'])
def test_auth_dialog_sources_validation_and_cancel(qt_app, tmp_path, monkeypatch, language, fake_session_import):
    from app.auth_dialog import AuthDialog, QFileDialog
    dialog = AuthDialog(language, {})
    dialog.show()
    assert not dialog.browser.isVisible() and not dialog.file_row.isVisible()
    dialog.source.setCurrentIndex(dialog.source.findData('browser'))
    assert dialog.browser.isVisible() and dialog.profile.isVisible()
    assert not dialog.file_row.isVisible()
    dialog.browser.setCurrentIndex(dialog.browser.findData('firefox'))
    dialog.profile.setText('Profile 2')
    dialog._apply()
    assert dialog.auth == {'source': 'browser', 'browser': 'firefox', 'profile': 'Profile 2'}
    dialog.close()
    dialog = AuthDialog(language, {})
    dialog.source.setCurrentIndex(dialog.source.findData('file'))
    dialog._apply()
    assert dialog.result() == 0 and dialog.error.text()
    cookie_file = tmp_path / 'export.txt'
    cookie_file.write_text('# HTTP Cookie File\n')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *_: (str(cookie_file), ''))
    dialog.browse.click()
    dialog._apply()
    assert dialog.auth == {'source': 'file', 'path': str(cookie_file)}
    dialog.close()


def test_auth_selection_persists_invalidation_and_dispatch(qt_app, monkeypatch, isolated_settings, fake_session_import):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from test_media import selectable_info
    window = Window('en')
    window.show()
    info = selectable_info() | {'url': 'https://www.youtube.com/watch?v=abcdefghijk'}
    window.url.setText(info['url'])
    window._show_info(info)
    def choose():
        dialog = QApplication.activeModalWidget()
        dialog.source.setCurrentIndex(dialog.source.findData('browser'))
        dialog.browser.setCurrentIndex(dialog.browser.findData('firefox'))
        dialog.profile.setText('Test profile')
        dialog._apply()
    QTimer.singleShot(0, choose)
    window.authentication.click()
    assert window._info is None and not window.preview.isVisible()
    assert 'Saved session' in window.authentication.text()
    assert 'Test profile' in isolated_settings().value('authentication')
    fresh = Window('en')
    assert fresh._auth == window._auth
    fresh.close()
    calls = []
    monkeypatch.setattr(window.downloader, 'start', lambda *args, **kw: calls.append(kw))
    window.download.click()
    assert calls[-1]['action'] == 'inspect' and calls[-1]['auth']['source'] == 'file'
    assert calls[-1]['auth']['path'] == str(window.session_folder / 'cookies.txt')
    window._show_info(info)
    window.confirm.click()
    assert calls[-1]['auth']['source'] == 'file' and 'action' not in calls[-1]
    window._busy(True)
    assert not window.authentication.isEnabled()
    window._busy(False)
    previous = window._auth.copy()
    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
    window.authentication.click()
    assert window._auth == previous and window._info == info
    def disable():
        dialog = QApplication.activeModalWidget()
        dialog.source.setCurrentIndex(dialog.source.findData('none'))
        dialog._apply()
    QTimer.singleShot(0, disable)
    window.authentication.click()
    assert window._auth == {} and not isolated_settings().contains('authentication')
    assert window._info is None
    window._finished('error', 'auth_file_invalid')
    assert 'Netscape' in window.detail.text()
    window.close()


def test_language_switch_preserves_preview_selection_and_restores_after_restart(qt_app, isolated_settings, monkeypatch):
    from test_media import selectable_info
    window = Window('en')
    window.show()
    info = selectable_info() | {'url': 'https://www.youtube.com/watch?v=abcdefghijk'}
    window.url.setText(info['url'])
    window._show_info(info)
    window.quality.setCurrentIndex(window.quality.findData('low'))
    window.container.setCurrentIndex(window.container.findData('mkv'))
    before = window._selection.copy()
    calls = []
    monkeypatch.setattr(window.downloader, 'start', lambda *args, **kw: calls.append(kw))
    for language in ('ru', 'uk', 'en'):
        window.language_combo.setCurrentIndex(window.language_combo.findData(language))
        assert window.language == language
        assert window.download.text() == translate(language, 'analysis')
        assert window.confirm.text() == translate(language, 'confirm')
        assert window.video_check.text() == translate(language, 'video')
        assert window.language_label.text() == translate(language, 'language')
        assert window.destination.text() == translate(language, 'destination', folder=window.downloads)
        assert window.status.text() == translate(language, 'inspected')
        assert translate(language, 'duration', value='1:01')[:5] in window.video_meta.text()
        assert window._selection == before and window._info == info and window.preview.isVisible()
        assert window.container.currentData() == 'mkv'
        assert window.quality.currentData() == 'low'
        assert not calls
        fresh = Window()
        assert fresh.language == language and fresh.language_combo.currentData() == language
        fresh.close()
    window.language_combo.setCurrentIndex(window.language_combo.findData('uk'))
    settings_path = isolated_settings().fileName()
    result = subprocess.run([sys.executable, '-c',
        'from PySide6.QtCore import QSettings; import sys; '
        'print(QSettings(sys.argv[1], QSettings.Format.IniFormat).value("ui_language"))', settings_path],
        capture_output=True, text=True, encoding='utf-8', check=True, timeout=10)
    assert result.stdout.strip() == 'uk'
    # Source-specific dialogs use the newly selected language too.
    from app.auth_dialog import AuthDialog
    dialog = AuthDialog(window.language, window._auth, window)
    assert dialog.windowTitle() == translate('uk', 'auth_title')
    dialog.close()
    window.close()


def test_language_switch_keeps_active_download_and_translates_progress(qt_app, monkeypatch):
    window = Window('en')
    window.url.setText('https://youtu.be/abcdefghijk')
    window.downloader.busy = True
    window._busy(True)
    calls = []
    monkeypatch.setattr(window.downloader, 'start', lambda *a, **kw: calls.append('start'))
    monkeypatch.setattr(window.downloader, 'cancel', lambda: calls.append('cancel'))
    window._event({'type': 'progress', 'downloaded': 50, 'total': 100,
                   'speed': 1024, 'eta': 62, 'format': '137'})
    window.language_combo.setCurrentIndex(window.language_combo.findData('ru'))
    assert window.downloader.busy and window.progress.value() == 500
    assert window.status.text() == translate('ru', 'downloading', stream=1, percent='50.0%')
    assert 'Осталось 01:02' in window.detail.text()
    assert window._formats == ['137'] and not calls
    assert not window.url.isEnabled() and not window.download.isEnabled() and window.cancel.isEnabled()
    window.downloader.busy = False
    window._busy(False)
    window.close()


def test_choosing_current_language_still_saves_preference(qt_app, isolated_settings):
    window = Window('en')
    assert not isolated_settings().contains('ui_language')
    window.language_combo.activated.emit(window.language_combo.currentIndex())
    assert isolated_settings().value('ui_language') == 'en'
    window.close()


def test_language_switch_preserves_result_errors_and_localizes_messages(qt_app):
    window = Window('en')
    window.show()
    window._finished('success', '/tmp/Моє відео.mp4')
    window.language_combo.setCurrentIndex(window.language_combo.findData('ru'))
    assert window.status.text() == translate('ru', 'success')
    assert window.detail.text() == 'Моє відео.mp4'
    assert window.open_folder.isVisible()
    assert window.result_path == Path('/tmp/Моє відео.mp4')
    window._finished('error', 'auth_file_invalid')
    window.language_combo.setCurrentIndex(window.language_combo.findData('uk'))
    assert window.detail.text() == translate('uk', 'auth_file_invalid')
    window._auth = {'source': 'browser', 'browser': 'firefox', 'profile': ''}
    window._finished('error', 'Upstream diagnostic')
    window.language_combo.setCurrentIndex(window.language_combo.findData('en'))
    assert window.detail.text() == 'Upstream diagnostic\n\n' + translate('en', 'auth_error_hint')
    window.close()


@pytest.mark.parametrize('saved', ['', 'de', 'uk'])
def test_saved_language_fallback_and_explicit_launch_override(qt_app, isolated_settings, monkeypatch, saved):
    from PySide6.QtCore import QLocale
    settings = isolated_settings()
    settings.setValue('ui_language', saved)
    settings.sync()
    monkeypatch.setattr('app.window.QLocale.system', lambda: QLocale('ru_RU'))
    window = Window()
    assert window.language == ('uk' if saved == 'uk' else 'ru')
    window.close()
    override = Window('en')
    assert override.language == 'en'
    assert isolated_settings().value('ui_language') == saved
    override.close()


def test_startup_update_check_notice_and_controls(qt_app, monkeypatch):
    from app.downloader import Downloader
    from test_media import selectable_info
    calls = []
    def check(controller, components):
        calls.append(components)
        controller.busy = True
        controller.busy_changed.emit(True)
    monkeypatch.setattr(Downloader, 'check_updates', check)
    window = Window('en')
    window.show()
    qt_app.processEvents()
    assert calls == [window.components]
    assert window.updater.busy and not window.download.isEnabled()
    assert window.language_combo.isEnabled() and window.url.isEnabled()
    info = selectable_info() | {'url': 'https://www.youtube.com/watch?v=abcdefghijk'}
    window._show_info(info)
    assert not window.confirm.isEnabled()
    window.updater.event.emit({'type': 'update_checked', 'status': 'available', 'version': '2099.01.02'})
    window.updater.busy = False
    window.updater.busy_changed.emit(False)
    window.updater.finished.emit('update_checked', '')
    assert window.download.isEnabled() and window.confirm.isEnabled()
    assert '2099.01.02' in window.update_notice.text()
    window.language_combo.setCurrentIndex(window.language_combo.findData('ru'))
    assert window.update_notice.text() == translate('ru', 'update_available', version='2099.01.02')
    window._event({'type': 'update', 'status': 'failed', 'version': '2099.01.02'})
    assert window.update_notice.text() == translate('ru', 'update_failed')
    window.close()


def test_closing_app_cancels_background_update_check(qt_app, monkeypatch):
    from app.downloader import Downloader
    calls = []
    def check(controller, components):
        controller.busy = True
        controller.busy_changed.emit(True)
    monkeypatch.setattr(Downloader, 'check_updates', check)
    window = Window('en')
    window.show()
    qt_app.processEvents()
    monkeypatch.setattr(window.updater, 'cancel', lambda: calls.append('cancel'))
    window.close()
    assert calls == ['cancel'] and window.isVisible()
    window.updater.busy = False
    window.updater.finished.emit('cancelled', '')
    qt_app.processEvents()
    assert not window.isVisible()
