from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .controller import AppController
from .paths import AppPaths


class LauncherWindow(QMainWindow):
    def __init__(self, controller: AppController, paths: AppPaths) -> None:
        super().__init__()
        self.controller = controller
        self.paths = paths
        self._is_busy = False
        self._is_running = False
        self.setWindowTitle("Screen Commentator Launcher")
        self.setMinimumSize(560, 380)

        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("Screen Commentator Windows")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Install llmster and the fixed multimodal model once, then use Start / Stop to control comments."
        )
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        config_label = QLabel(f"Config: {self.paths.config_file}")
        config_label.setWordWrap(True)
        config_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(config_label)

        self.status_label = QLabel("Idle")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("padding: 8px; background: #f4f4f4; border-radius: 6px;")
        layout.addWidget(self.status_label)

        self.progress_label = QLabel("")
        self.progress_label.hide()
        layout.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        button_row = QHBoxLayout()
        self.install_button = QPushButton("Install")
        self.start_button = QPushButton("Start")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)

        self.install_button.clicked.connect(self.controller.install)
        self.start_button.clicked.connect(self.controller.start)
        self.stop_button.clicked.connect(self.controller.stop)

        button_row.addWidget(self.install_button)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        layout.addLayout(button_row)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        layout.addWidget(self.log_view, 1)

        self.setCentralWidget(central)
        self._connect_signals()

    def _connect_signals(self) -> None:
        self.controller.signals.status_changed.connect(self._on_status_changed)
        self.controller.signals.log_message.connect(self._append_log)
        self.controller.signals.busy_changed.connect(self._set_busy)
        self.controller.signals.running_changed.connect(self._set_running)
        self.controller.signals.progress_label_changed.connect(self._set_progress_label)
        self.controller.signals.progress_value_changed.connect(self._set_progress_value)
        self.controller.signals.progress_indeterminate_changed.connect(self._set_progress_mode)
        self.controller.signals.progress_visibility_changed.connect(self._set_progress_visible)

    def _on_status_changed(self, message: str) -> None:
        self.status_label.setText(message)

    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def _set_busy(self, busy: bool) -> None:
        self._is_busy = busy
        self._refresh_buttons()

    def _set_running(self, running: bool) -> None:
        self._is_running = running
        self._refresh_buttons()

    def _set_progress_label(self, label: str) -> None:
        self.progress_label.setText(label)

    def _set_progress_value(self, value: int) -> None:
        if self.progress_bar.maximum() > 0:
            self.progress_bar.setValue(value)

    def _set_progress_mode(self, indeterminate: bool) -> None:
        if indeterminate:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)

    def _set_progress_visible(self, visible: bool) -> None:
        self.progress_label.setVisible(visible)
        self.progress_bar.setVisible(visible)

    def _refresh_buttons(self) -> None:
        self.install_button.setEnabled(not self._is_busy and not self._is_running)
        self.start_button.setEnabled(not self._is_busy and not self._is_running)
        self.stop_button.setEnabled(not self._is_busy and self._is_running)
