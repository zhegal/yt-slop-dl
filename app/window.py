"""One native Qt window; no webview or network work in the GUI thread."""

import math
import base64
import json
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QLocale, QSettings, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from app.downloader import Downloader, validate_url
from app.auth import AUTH_ERRORS, normalize_auth
from app.auth_dialog import AuthDialog
from app.i18n import LANGUAGES, language_for, translate
from app.media import format_duration, select_streams
from app import APP_NAME


def readable_size(value: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PiB"


def number(value):
    return value if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 else None


class Window(QWidget):
    def __init__(self, language: str | None = None):
        super().__init__()
        self._settings = QSettings()
        saved_language = self._settings.value("ui_language", "", type=str)
        self.language = language_for(language or (saved_language if saved_language in LANGUAGES else QLocale.system().name()))
        self._status_text = ("ready", {})
        self._detail_text = ("hint", {})
        self._progress_detail = None
        self._last_error = ""
        try:
            self._auth = normalize_auth(json.loads(self._settings.value("authentication", "{}", type=str)))
        except (ValueError, TypeError):
            self._auth = {}
            self._settings.remove("authentication")
        self._default_downloads = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or Path.home() / "Downloads")
        self.downloads = self._saved_destination()
        self.components = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "components"
        # Reuse the tools installed before the application was renamed.
        legacy = self.components.parent.parent / "Media Downloader" / "components"
        if not self.components.exists() and legacy.exists():
            self.components = legacy
        self.downloader = Downloader(self)
        self.downloader.event.connect(self._event)
        self.downloader.finished.connect(self._finished)
        self.downloader.busy_changed.connect(self._busy)
        self._closing = False
        self._formats = []
        self._info = None
        self._selection = None
        self.result_path = None
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(str(Path(__file__).parent / "assets" / "icon.svg")))
        self.resize(700, 420)
        # Let the layout calculate the minimum height as visible content changes.
        self.setMinimumWidth(580)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 30, 32, 24)
        layout.setSpacing(18)
        self.title = QLabel(self.t("title"))
        font = QFont(self.font())
        font.setPointSize(21)
        font.setWeight(QFont.Weight.DemiBold)
        self.title.setFont(font)
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.subtitle = QLabel(self.t("subtitle"))
        self.subtitle.setObjectName("muted")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.url = QLineEdit()
        self.url.setObjectName("videoUrl")
        self.url.setPlaceholderText("https://www.youtube.com/watch?v=…")
        self.url.setAccessibleName(self.t("url"))
        self.url.setClearButtonEnabled(True)
        self.url.setMinimumHeight(44)
        self.url.returnPressed.connect(self._start)
        self.url.textChanged.connect(self._link_changed)
        self.download = QPushButton(self.t("analysis"))
        self.download.setObjectName("primary")
        self.download.setMinimumHeight(44)
        self.download.setMinimumWidth(130)
        self.download.setDefault(True)
        self.download.clicked.connect(self._start)
        row.addWidget(self.url, 1)
        row.addWidget(self.download)
        layout.addLayout(row)
        self.authentication = QPushButton()
        self.authentication.setMinimumHeight(32)
        self.authentication.clicked.connect(self._configure_auth)
        self._update_auth_button()
        options_row = QHBoxLayout()
        options_row.addWidget(self.authentication)
        options_row.addStretch()
        self.language_label = QLabel(self.t("language"))
        self.language_combo = QComboBox()
        self.language_combo.setAccessibleName(self.t("language"))
        self.language_combo.setMinimumHeight(32)
        for code, name in LANGUAGES.items():
            self.language_combo.addItem(name, code)
        self.language_combo.setCurrentIndex(self.language_combo.findData(self.language))
        self.language_combo.currentIndexChanged.connect(self._change_language)
        # Selecting the already displayed system language is an explicit choice too.
        self.language_combo.activated.connect(self._change_language)
        self.language_label.setBuddy(self.language_combo)
        options_row.addWidget(self.language_label)
        options_row.addWidget(self.language_combo)
        layout.addLayout(options_row)

        self.preview = QFrame()
        self.preview.setObjectName("preview")
        preview_layout = QVBoxLayout(self.preview)
        preview_layout.setContentsMargins(18, 16, 18, 16)
        preview_layout.setSpacing(12)
        video_row = QHBoxLayout()
        video_row.setSpacing(16)
        self.thumbnail = QLabel(self.t("no_thumbnail"))
        self.thumbnail.setFixedSize(176, 100)
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail.setWordWrap(True)
        video_row.addWidget(self.thumbnail)
        description = QVBoxLayout()
        self.video_title = QLabel()
        self.video_title.setTextFormat(Qt.TextFormat.PlainText)
        self.video_title.setWordWrap(True)
        video_font = QFont(self.font())
        video_font.setWeight(QFont.Weight.DemiBold)
        self.video_title.setFont(video_font)
        self.video_meta = QLabel()
        self.video_meta.setObjectName("muted")
        self.video_meta.setWordWrap(True)
        self.video_meta.setTextFormat(Qt.TextFormat.PlainText)
        description.addWidget(self.video_title)
        description.addWidget(self.video_meta)
        video_row.addLayout(description, 1)
        preview_layout.addLayout(video_row)
        self.qualities = QLabel()
        self.qualities.setObjectName("muted")
        self.qualities.setWordWrap(True)
        preview_layout.addWidget(self.qualities)
        quality_row = QHBoxLayout()
        self.quality_label = QLabel(self.t("quality"))
        quality_row.addWidget(self.quality_label)
        self.quality = QComboBox()
        self.quality.setAccessibleName(self.t("quality"))
        self.quality.setMinimumHeight(36)
        self.quality.currentIndexChanged.connect(self._refresh_selection)
        quality_row.addWidget(self.quality, 1)
        self.video_check = QCheckBox(self.t("video"))
        self.audio_check = QCheckBox(self.t("audio"))
        for checkbox in (self.video_check, self.audio_check):
            checkbox.setChecked(True)
            checkbox.toggled.connect(self._refresh_quality_options)
            quality_row.addWidget(checkbox)
        preview_layout.addLayout(quality_row)
        selection = QHBoxLayout()
        self.container_label = QLabel(self.t("container"))
        selection.addWidget(self.container_label)
        self.container = QComboBox()
        self.container.setAccessibleName(self.t("container"))
        self.container.setMinimumHeight(36)
        selection.addWidget(self.container, 1)
        self.confirm = QPushButton(self.t("confirm"))
        self.confirm.setObjectName("primary")
        self.confirm.setMinimumHeight(40)
        self.confirm.clicked.connect(self._confirm_download)
        selection.addWidget(self.confirm)
        preview_layout.addLayout(selection)
        self.container_note = QLabel(self.t("container_hint"))
        self.container_note.setObjectName("muted")
        self.container_note.setWordWrap(True)
        preview_layout.addWidget(self.container_note)
        self.preview.hide()
        layout.addWidget(self.preview)

        card = QFrame()
        card.setObjectName("card")
        inner = QVBoxLayout(card)
        inner.setContentsMargins(18, 16, 18, 16)
        inner.setSpacing(12)
        status_row = QHBoxLayout()
        self.status = QLabel(self.t("ready"))
        self.status.setWordWrap(True)
        status_font = QFont(self.font())
        status_font.setWeight(QFont.Weight.DemiBold)
        self.status.setFont(status_font)
        self.status.setMinimumHeight(self.status.fontMetrics().height() + 4)
        self.cancel = QPushButton(self.t("cancel"))
        self.cancel.setMinimumHeight(32)
        self.cancel.clicked.connect(self._cancel)
        self.cancel.hide()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.cancel)
        inner.addLayout(status_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress.setAccessibleName(self.t("preparing"))
        inner.addWidget(self.progress)
        self.detail = QLabel(self.t("hint"))
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        inner.addWidget(self.detail)
        self.open_folder = QPushButton(self.t("open"))
        self.open_folder.setMinimumHeight(36)
        self.open_folder.clicked.connect(self._open_folder)
        self.open_folder.hide()
        inner.addWidget(self.open_folder, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(card)
        folder_row = QHBoxLayout()
        self.destination = QLabel(self.t("destination", folder=self.downloads))
        self.destination.setObjectName("muted")
        self.destination.setWordWrap(True)
        self.destination.setTextFormat(Qt.TextFormat.PlainText)
        self.destination.setToolTip(str(self.downloads))
        self.choose_folder = QPushButton(self.t("choose_folder"))
        self.choose_folder.setMinimumHeight(36)
        self.choose_folder.clicked.connect(self._choose_folder)
        folder_row.addWidget(self.destination, 1)
        folder_row.addWidget(self.choose_folder)
        layout.addLayout(folder_row)
        self.folder_watcher = QFileSystemWatcher(self)
        self.folder_watcher.directoryChanged.connect(self._check_destination)
        self._check_destination()
        self.footer = QLabel(self.t("first_run"))
        self.footer.setObjectName("muted")
        self.footer.setWordWrap(True)
        layout.addWidget(self.footer)
        layout.addStretch()
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._theme)
        self._theme()
        self.url.setFocus()

    def t(self, key, **values):
        return translate(self.language, key, **values)

    def _set_status(self, key, **values):
        self._status_text = (key, values)
        self.status.setText(self.t(key, **values))
        self.progress.setAccessibleName(self.status.text())

    def _set_detail(self, key=None, *, text="", **values):
        self._detail_text = (key, values if key else {"text": text})
        self._progress_detail = None
        self.detail.setText(self.t(key, **values) if key else text)

    def _change_language(self, *_):
        language = self.language_combo.currentData()
        if language not in LANGUAGES:
            return
        self._settings.setValue("ui_language", language)
        self._settings.sync()
        if language == self.language:
            return
        self.language = language
        for widget, key in (
            (self.title, "title"), (self.subtitle, "subtitle"), (self.download, "analysis"),
            (self.cancel, "cancel"), (self.confirm, "confirm"), (self.open_folder, "open"),
            (self.video_check, "video"), (self.audio_check, "audio"), (self.quality_label, "quality"),
            (self.container_label, "container"), (self.container_note, "container_hint"),
            (self.choose_folder, "choose_folder"), (self.footer, "first_run"), (self.language_label, "language"),
        ):
            widget.setText(self.t(key))
        for widget, key in ((self.url, "url"), (self.quality, "quality"), (self.container, "container"),
                            (self.language_combo, "language")):
            widget.setAccessibleName(self.t(key))
        self._update_auth_button()
        self.destination.setText(self.t("destination", folder=self.downloads))
        # Repaint labels in place: changing language must preserve a running job,
        # its progress, preview, chosen formats and saved file.
        self._set_status(self._status_text[0], **self._status_text[1])
        if self._status_text[0] == "error":
            self._show_error(self._last_error)
        elif self._progress_detail is not None:
            self._set_progress_detail(self._progress_detail)
        else:
            self._set_detail(self._detail_text[0], **self._detail_text[1])
        if self._info:
            self._update_video_meta()
            if self.thumbnail.text():
                self.thumbnail.setText(self.t("no_thumbnail"))
            video = self.video_check.isChecked()
            options = self._info["video_options"] if video else self._info["audio_options"]
            for index, option in enumerate(options):
                self.quality.setItemText(index, self._quality_text(option, index, video))
            self.container.setItemText(0, self.t("auto" if video else "auto_audio"))
            for index in range(1, self.container.count()):
                name = self.container.itemData(index).upper()
                enabled = self.container.model().item(index).isEnabled()
                self.container.setItemText(index, name if enabled else self.t("unavailable", container=name))
            if not self._selection:
                self.qualities.setText(self.detail.text())
            else:
                codecs = [self._selection[key].upper() for key in ("vcodec", "acodec") if self._selection[key] != "none"]
                self.qualities.setText(self.t("selected_streams", value=" + ".join(codecs)))

    def _theme(self, *_):
        palette = self.palette()
        dark = palette.color(QPalette.ColorRole.Window).lightness() < 128
        border, muted = ("#40444d", "#b5bcc9") if dark else ("#dce0e7", "#646c7c")
        self.setStyleSheet(f"""
            QLabel#muted {{ color: {muted}; }}
            QFrame#card, QFrame#preview {{ border: 1px solid {border}; border-radius: 12px; }}
            QLineEdit#videoUrl {{ padding: 0 12px; border: 1px solid {border}; border-radius: 8px; background: palette(base); }}
            QLineEdit#videoUrl:focus {{ border: 1px solid #2780ff; }}
            QPushButton {{ padding: 5px 14px; }}
            QPushButton#primary {{ background: #1476ef; color: white; border: none; border-radius: 8px; font-weight: 600; }}
            QPushButton#primary:hover {{ background: #0864d5; }}
            QPushButton#primary:pressed {{ background: #0554b4; }}
            QPushButton#primary:disabled {{ background: {border}; color: {muted}; }}
            QProgressBar {{ background: {border}; border: none; border-radius: 4px; }}
            QProgressBar::chunk {{ background: #2780ff; border-radius: 4px; }}
        """)

    def _start(self):
        if self.downloader.busy:
            return
        try:
            url = validate_url(self.url.text())
        except ValueError:
            self._set_status("invalid_url")
            self.url.setFocus()
            return
        self.result_path = None
        self._info = None
        self._selection = None
        self.preview.hide()
        self.open_folder.hide()
        self._formats = []
        self._set_status("preparing")
        self._set_detail("first_run")
        self.progress.setRange(0, 0)
        try:
            self.downloader.start(url, self.downloads, self.components, action="inspect", auth=self._auth)
        except (OSError, ValueError) as error:
            self._finished("error", str(error))

    def _link_changed(self):
        self._info = None
        self._selection = None
        self.preview.hide()
        self.confirm.setEnabled(False)
        if not self.downloader.busy:
            self._set_status("ready")
            self._set_detail("hint")
            self.progress.setRange(0, 1000)
            self.progress.setValue(0)
            self.open_folder.hide()

    def _show_info(self, info):
        self._info = info
        self.video_title.setText(info["title"])
        resolution = f"{info['height']}p" if info.get("height") else "—"
        self._update_video_meta()
        self.qualities.setText(self.t("qualities", values=", ".join(f"{height}p" for height in info.get("resolutions", [])) or resolution))
        self.thumbnail.clear()
        self.thumbnail.setText(self.t("no_thumbnail"))
        if info.get("thumbnail"):
            pixmap = QPixmap()
            try:
                data = base64.b64decode(info["thumbnail"], validate=True)
                if pixmap.loadFromData(data):
                    self.thumbnail.setPixmap(pixmap.scaled(self.thumbnail.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            except ValueError:
                pass
        self._refresh_quality_options()
        self.preview.show()
        self.resize(max(self.width(), 740), max(self.height(), 680))

    def _update_video_meta(self):
        meta = [self._info.get("channel", ""), self.t("duration", value=format_duration(self._info.get("duration")))]
        self.video_meta.setText("\n".join(part for part in meta if part))

    def _quality_text(self, option, index, video):
        if video:
            label = f"{option.get('height') or '—'}p"
            if option.get("fps"):
                label += f" · {option['fps']:g} fps"
        else:
            label = option["acodec"].upper()
            if option.get("abr"):
                label += f" · {option['abr']:.0f} kbit/s"
            if option.get("drc"):
                label += " · DRC"
        return self.t("best_quality", value=label) if index == 0 else label

    def _refresh_quality_options(self, *_):
        if not self._info:
            return
        previous = self.quality.currentData()
        video = self.video_check.isChecked()
        options = self._info["video_options"] if video else self._info["audio_options"]
        self.quality.blockSignals(True)
        self.quality.clear()
        for index, option in enumerate(options):
            self.quality.addItem(self._quality_text(option, index, video), str(option["format_id"]))
        restored = self.quality.findData(previous)
        if restored >= 0:
            self.quality.setCurrentIndex(restored)
        self.quality.blockSignals(False)
        self._refresh_selection()

    def _refresh_selection(self, *_):
        if not self._info:
            return
        self._selection = None
        video, audio = self.video_check.isChecked(), self.audio_check.isChecked()
        try:
            self._selection = select_streams(self._info, self.quality.currentData(), video, audio)
        except ValueError as error:
            self._set_detail(str(error))
        previous = self.container.currentData()
        self.container.clear()
        self.container.addItem(self.t("auto") if video else self.t("auto_audio"), "auto")
        names = ("mkv", "mp4", "webm") if video else ("m4a", "opus", "mkv", "mp4", "webm")
        for name in names:
            available = self._selection is not None and name in self._selection["containers"]
            self.container.addItem(name.upper() if available else self.t("unavailable", container=name.upper()), name)
            self.container.model().item(self.container.count() - 1).setEnabled(available)
        if self._selection:
            if previous in self._selection["containers"]:
                self.container.setCurrentIndex(self.container.findData(previous))
            codecs = [self._selection[key].upper() for key in ("vcodec", "acodec") if self._selection[key] != "none"]
            self.qualities.setText(self.t("selected_streams", value=" + ".join(codecs)))
            if not self.downloader.busy:
                self._set_status("inspected")
                self._set_detail("choose_container")
        else:
            self.qualities.setText(self.detail.text())
        self.confirm.setEnabled(not self.downloader.busy and self._selection is not None)
        self.quality.setEnabled(not self.downloader.busy and (video or audio))
        self.container.setEnabled(not self.downloader.busy and self._selection is not None)

    def _confirm_download(self):
        if self.downloader.busy or not self._info or not self._selection:
            return
        try:
            if validate_url(self.url.text()) != self._info["url"]:
                self._link_changed()
                return
            container = self.container.currentData()
            if container != "auto" and container not in self._selection["containers"]:
                return
            self._check_destination()
            self._formats = []
            self.open_folder.hide()
            self._set_status("preparing")
            self._set_detail()
            self.progress.setRange(0, 0)
            self.downloader.start(self._info["url"], self.downloads, self.components,
                                  container=container, format_id=self._selection["format_id"],
                                  video=self.video_check.isChecked(), audio=self.audio_check.isChecked(), auth=self._auth)
        except (OSError, ValueError) as error:
            self._finished("error", str(error))

    def _busy(self, busy):
        self.url.setEnabled(not busy)
        self.download.setEnabled(not busy)
        self.cancel.setVisible(busy)
        self.cancel.setEnabled(busy)
        self.confirm.setEnabled(not busy and self._selection is not None)
        self.container.setEnabled(not busy and self._selection is not None)
        self.quality.setEnabled(not busy and self._info is not None and (self.video_check.isChecked() or self.audio_check.isChecked()))
        self.video_check.setEnabled(not busy)
        self.audio_check.setEnabled(not busy)
        self.choose_folder.setEnabled(not busy)
        self.authentication.setEnabled(not busy)
        if not busy:
            self._check_destination()

    def _cancel(self):
        self._set_status("cancelling")
        self.cancel.setEnabled(False)
        self.downloader.cancel()

    def _event(self, event):
        if self.downloader._cancelled:
            return
        kind = event.get("type")
        if kind == "metadata":
            self._show_info(event["info"])
        elif kind == "dependency":
            percent = event.get("percent")
            self._set_status("dependency", component=event["component"], percent=f"{percent}%" if percent is not None else "")
            self.progress.setRange(0, 1000 if percent is not None else 0)
            if percent is not None:
                self.progress.setValue(percent * 10)
        elif kind in {"extracting", "processing", "saving"}:
            self._set_status(kind)
            self._set_detail()
            self.progress.setRange(0, 0)
        elif kind == "progress":
            downloaded, total = number(event.get("downloaded")), number(event.get("total"))
            fraction = min(downloaded / total, 1) if downloaded is not None and total else None
            format_id = event.get("format")
            if format_id not in self._formats:
                self._formats.append(format_id)
            percent = f"{fraction * 100:.1f}%" if fraction is not None else "…"
            self._set_status("downloading", stream=len(self._formats), percent=percent)
            self.progress.setRange(0, 1000 if fraction is not None else 0)
            if fraction is not None:
                self.progress.setValue(int(fraction * 1000))
            self._set_progress_detail(event)
        self.progress.setAccessibleName(self.status.text())

    def _set_progress_detail(self, event):
        downloaded = number(event.get("downloaded"))
        parts = [readable_size(downloaded)] if downloaded is not None else []
        speed, eta = number(event.get("speed")), number(event.get("eta"))
        if speed is not None:
            parts.append(self.t("speed", value=readable_size(speed)))
        if eta is not None:
            minutes, seconds = divmod(int(eta), 60)
            parts.append(self.t("eta", value=f"{minutes:02}:{seconds:02}"))
        if parts:
            self._set_detail(text=" · ".join(parts))
        else:
            self._set_detail("unknown")
        self._progress_detail = event.copy()

    def _finished(self, outcome, message):
        self.progress.setRange(0, 1000)
        self.progress.setValue(1000 if outcome == "success" else 0)
        self._set_status(outcome)
        if outcome == "inspected":
            self._set_detail("choose_container")
        elif outcome == "success":
            self.result_path = Path(message)
            self._set_detail(text=self.result_path.name)
            self.open_folder.show()
        else:
            self._last_error = message
            self._show_error(message, auth_hint=outcome == "error")
        self.progress.setAccessibleName(self.status.text())
        if self._closing:
            QTimer.singleShot(0, self.close)

    def _show_error(self, message, *, auth_hint=True):
        # Keep upstream diagnostics; translate only the application's own messages.
        if message in AUTH_ERRORS:
            self._set_detail(message)
            message = self.detail.text()
        elif not message:
            self._set_detail("retry")
        else:
            if auth_hint and self._auth:
                message += "\n\n" + self.t("auth_error_hint")
            self._set_detail(text=message[:1000])
        self.detail.setToolTip(message)

    def _update_auth_button(self):
        source = self.t("auth_none")
        if self._auth.get("source") == "browser":
            source = self._auth["browser"].capitalize()
        elif self._auth.get("source") == "file":
            source = self.t("auth_file")
        self.authentication.setText(self.t("auth_button", source=source))

    def _configure_auth(self):
        if self.downloader.busy:
            return
        dialog = AuthDialog(self.language, self._auth, self)
        if dialog.exec() == AuthDialog.DialogCode.Accepted and dialog.auth != self._auth:
            self._auth = dialog.auth
            if self._auth:
                self._settings.setValue("authentication", json.dumps(self._auth, ensure_ascii=False))
            else:
                self._settings.remove("authentication")
            self._settings.sync()
            self._update_auth_button()
            # Formats and access can change with the account. Require analysis again.
            self._link_changed()

    def _saved_destination(self):
        saved = self._settings.value("download_folder", "", type=str)
        if saved:
            try:
                folder = Path(saved)
                if folder.is_absolute() and folder.is_dir():
                    return folder
            except (OSError, ValueError):
                pass
            # Clear the stale preference, so recreating the old path later
            # does not unexpectedly restore it as the destination.
            self._settings.remove("download_folder")
            self._settings.sync()
        return self._default_downloads

    def _check_destination(self, *_):
        if self.downloader.busy:
            return
        self.downloads = self._saved_destination()
        self.destination.setText(self.t("destination", folder=self.downloads))
        self.destination.setToolTip(str(self.downloads))
        desired = [str(self.downloads)] if self.downloads != self._default_downloads else []
        current = self.folder_watcher.directories()
        if current != desired:
            if current:
                self.folder_watcher.removePaths(current)
            if desired:
                self.folder_watcher.addPaths(desired)

    def _choose_folder(self):
        if self.downloader.busy:
            return
        self._check_destination()
        folder = QFileDialog.getExistingDirectory(self, self.t("choose_folder"), str(self.downloads))
        if folder:
            self._settings.setValue("download_folder", folder)
            self._settings.sync()
            self._check_destination()

    def _open_folder(self):
        folder = self.result_path.parent if self.result_path else self.downloads
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            self._set_detail("open_error", folder=folder)

    def closeEvent(self, event):
        if self.downloader.busy:
            self._closing = True
            self._cancel()
            event.ignore()
        else:
            event.accept()
