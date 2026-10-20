"""Choose audio tracks and the one that should play by default."""

from PySide6.QtCore import QLocale, Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QRadioButton, QScrollArea, QVBoxLayout, QWidget,
)

from app.i18n import LANGUAGES, translate


def audio_track_text(track, language):
    code = track.get("language") or ""
    base = code.removesuffix("-desc")
    locale = QLocale(base.replace("-", "_"))
    name = LANGUAGES.get(base) or (locale.nativeLanguageName() if code and locale.language() != QLocale.Language.C else "")
    name = name[:1].upper() + name[1:]
    if name and "-" in base:
        name += f" [{base}]"
    label = name or track.get("name") or (code if code and code != "und" else translate(language, "unknown_audio_language"))
    if track.get("name") and track["name"].casefold() != label.casefold():
        label += " · " + track["name"]
    if track.get("original"):
        label += " · " + translate(language, "original_track")
    if code.endswith("-desc"):
        label += " · " + translate(language, "audio_description")
    return label


class AudioTracksDialog(QDialog):
    def __init__(self, language, tracks, selected_ids, parent=None):
        super().__init__(parent)
        self.language = language
        self.selected_ids = list(selected_ids)
        self.setWindowTitle(translate(language, "audio_tracks_title"))
        self.resize(540, 460)
        self.setMinimumSize(440, 300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        hint = QLabel(translate(language, "audio_tracks_hint"))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        header = QHBoxLayout()
        header.addWidget(QLabel(translate(language, "audio_tracks_title")), 1)
        header.addWidget(QLabel(translate(language, "primary_audio_track")))
        layout.addLayout(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        rows = QVBoxLayout(content)
        self.checks, self.primary_buttons = {}, {}
        self.primary_group = QButtonGroup(self)
        by_id = {track["id"]: track for track in tracks}
        order = [key for key in selected_ids if key in by_id] + [key for key in by_id if key not in selected_ids]
        self.order = order
        for key in order:
            label = audio_track_text(by_id[key], language)
            item = QWidget()
            item.setMinimumHeight(32)
            row = QHBoxLayout(item)
            row.setContentsMargins(0, 0, 0, 0)
            check = QCheckBox(label)
            check.setChecked(key in selected_ids)
            primary = QRadioButton()
            primary.setAccessibleName(translate(language, "primary_for_track", track=label))
            primary.setToolTip(primary.accessibleName())
            self.primary_group.addButton(primary)
            primary.setChecked(bool(selected_ids) and key == selected_ids[0])
            self.checks[key], self.primary_buttons[key] = check, primary
            check.toggled.connect(self._refresh)
            # Keep native controls at their natural heights. Stretching a macOS
            # checkbox shifts its indicator away from the text's visual center.
            row.addWidget(check, 1, Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(primary, 0, Qt.AlignmentFlag.AlignVCenter)
            rows.addWidget(item)
        rows.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        buttons = QDialogButtonBox()
        self.apply_button = buttons.addButton(translate(language, "apply_tracks"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(translate(language, "cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh()

    def _refresh(self, *_):
        checked = [key for key in self.order if self.checks[key].isChecked()]
        for key, button in self.primary_buttons.items():
            button.setEnabled(key in checked)
        if checked and not any(self.primary_buttons[key].isChecked() for key in checked):
            self.primary_buttons[checked[0]].setChecked(True)
        self.apply_button.setEnabled(bool(checked))

    def accept(self):
        checked = [key for key in self.order if self.checks[key].isChecked()]
        if not checked:
            return
        primary = next(key for key in checked if self.primary_buttons[key].isChecked())
        self.selected_ids = [primary, *(key for key in checked if key != primary)]
        super().accept()
