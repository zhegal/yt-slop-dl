"""A compact native dialog for choosing a yt-dlp cookie source."""

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from app.auth import BROWSERS, normalize_auth
from app.i18n import translate


class AuthDialog(QDialog):
    def __init__(self, language, auth, parent=None):
        super().__init__(parent)
        self.language = language
        self.auth = normalize_auth(auth)
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
        hint = QLabel(self.t("auth_hint"))
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(hint)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox()
        apply_button = buttons.addButton(self.t("auth_apply"), QDialogButtonBox.ButtonRole.AcceptRole)
        apply_button.setDefault(True)
        buttons.addButton(self.t("cancel"), QDialogButtonBox.ButtonRole.RejectRole)
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

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, self.t("auth_file"), self.file.text(), "Text (*.txt);;All files (*)")
        if path:
            self.file.setText(path)
            self.error.hide()

    def _apply(self):
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
        self.auth = auth
        self.accept()
