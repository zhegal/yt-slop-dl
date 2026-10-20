"""A compact native dialog for choosing a yt-dlp cookie source."""

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QStandardPaths
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from app.auth import AUTH_ERRORS, BROWSERS, normalize_auth
from app.downloader import Downloader
from app.i18n import translate


class AuthDialog(QDialog):
    def __init__(self, language, auth, parent=None):
        super().__init__(parent)
        self.language = language
        self.auth = normalize_auth(auth)
        data = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        self.session_folder = getattr(parent, "session_folder", data / "authorization")
        self.components = getattr(parent, "components", data / "components")
        self.downloader = Downloader(self)
        self.downloader.finished.connect(self._import_finished)
        self._pending = None
        self.setWindowTitle(self.t("auth_title"))
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        # Refit when source-specific rows appear; never compress the form/text.
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        form = QFormLayout()
        form.setVerticalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.source = QComboBox()
        for key in ("none", "browser", "file"):
            self.source.addItem(self.t("auth_" + key), key)
        self.source.setAccessibleName(self.t("auth_source"))
        form.addRow(self.t("auth_source"), self.source)
        self.browser = QComboBox()
        for name in BROWSERS:
            if name != "safari" or sys.platform == "darwin":
                self.browser.addItem(name.capitalize(), name)
        self.browser.setAccessibleName(self.t("auth_browser"))
        form.addRow(self.t("auth_browser"), self.browser)
        self.profile = QLineEdit()
        self.profile.setPlaceholderText(self.t("auth_profile_hint"))
        self.profile.setAccessibleName(self.t("auth_profile"))
        form.addRow(self.t("auth_profile"), self.profile)
        self.file_row = QWidget()
        row = QHBoxLayout(self.file_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.file = QLineEdit()
        self.file.setReadOnly(True)
        self.file.setAccessibleName(self.t("auth_file"))
        for field in (self.profile, self.file):
            field.setMinimumHeight(max(36, field.fontMetrics().height() + 14))
        self.browse = QPushButton(self.t("auth_browse"))
        self.browse.setAutoDefault(False)
        self.browse.clicked.connect(self._browse)
        row.addWidget(self.file, 1)
        row.addWidget(self.browse)
        form.addRow(self.t("auth_file"), self.file_row)
        layout.addLayout(form)
        self.saved = QLabel(self.t("auth_saved" if (self.session_folder / "cookies.txt").is_file() else "auth_import_required"))
        self.saved.setWordWrap(True)
        layout.addWidget(self.saved)
        hint = QLabel(self.t("auth_hint"))
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(hint)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox()
        self.apply_button = buttons.addButton(self.t("auth_import"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setDefault(True)
        self.cancel_button = buttons.addButton(self.t("cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.source.setCurrentIndex(self.source.findData(self.auth.get("source", "none")))
        self.browser.setCurrentIndex(max(0, self.browser.findData(self.auth.get("browser", "chrome"))))
        self.profile.setText(self.auth.get("profile", ""))
        self.file.setText(self.auth.get("path", ""))
        self.source.currentIndexChanged.connect(self._visibility)
        self._form = form
        self._visibility()

    def t(self, key):
        return translate(self.language, key)

    def _visibility(self, *_):
        source = self.source.currentData()
        for field, visible in ((self.browser, source == "browser"),
                               (self.profile, source == "browser"), (self.file_row, source == "file")):
            self._form.setRowVisible(field, visible)
        self.error.hide()
        exists = (self.session_folder / "cookies.txt").is_file()
        self.apply_button.setText(self.t("auth_disable" if source == "none" else "auth_reimport" if exists else "auth_import"))

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, self.t("auth_file"), self.file.text(), "Text (*.txt);;All files (*)")
        if path:
            self.file.setText(path)
            self.error.hide()

    def _apply(self):
        if self.downloader.busy:
            return
        source = self.source.currentData()
        try:
            auth = {} if source == "none" else normalize_auth(
                {"source": source, "browser": self.browser.currentData(), "profile": self.profile.text()}
                if source == "browser" else {"source": source, "path": self.file.text()})
            if source == "file" and not Path(auth["path"]).is_file():
                raise ValueError("auth_file_unreadable")
        except (ValueError, OSError) as error:
            key = str(error) if isinstance(error, ValueError) else "auth_file_unreadable"
            self.error.setText(self.t(key))
            self.error.show()
            return
        if not auth:
            try:
                (self.session_folder / "cookies.txt").unlink(missing_ok=True)
            except OSError:
                self.error.setText(self.t("auth_storage_failed"))
                self.error.show()
                return
            self.auth = {}
            self.accept()
            return
        self._pending = auth
        for field in (self.source, self.browser, self.profile, self.browse, self.apply_button):
            field.setEnabled(False)
        self.error.hide()
        self.saved.setText(self.t("auth_importing"))
        try:
            self.downloader.import_auth(auth, self.session_folder, self.components)
        except (OSError, ValueError):
            self._import_finished("error", "auth_storage_failed")

    def _import_finished(self, outcome, message):
        for field in (self.source, self.browser, self.profile, self.browse, self.apply_button):
            field.setEnabled(True)
        if outcome == "auth_imported":
            self.auth = self._pending
            self.accept()
        elif outcome == "cancelled":
            super().reject()
        else:
            self.saved.setText(self.t("auth_saved" if (self.session_folder / "cookies.txt").is_file() else "auth_import_required"))
            self.error.setText(self.t(message if message in AUTH_ERRORS else "auth_import_failed"))
            self.error.show()

    def reject(self):
        if self.downloader.busy:
            self.downloader.cancel()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.downloader.busy:
            self.downloader.cancel()
            event.ignore()
        else:
            super().closeEvent(event)
