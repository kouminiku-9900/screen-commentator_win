from __future__ import annotations

import ctypes
import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QTimer, Qt, Slot
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .models import ActiveComment, OverlayConfig, PendingComment, CommentStyle


GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080


class OverlayWindow(QWidget):
    def __init__(
        self,
        overlay_config: OverlayConfig,
        fixed_duration_sec: float,
        max_active: int,
        clock: Callable[[], float] | None = None,
    ) -> None:
        super().__init__()
        self.overlay_config = overlay_config
        self.fixed_duration_sec = fixed_duration_sec
        self.max_active = max_active
        self.clock = clock or time.monotonic
        self.active_comments: list[ActiveComment] = []
        self.last_used_lanes: list[int] = []

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setWindowTitle("Screen Commentator Overlay")

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())

        self._paint_timer = QTimer(self)
        self._paint_timer.setInterval(16)
        self._paint_timer.timeout.connect(self._tick)
        self._paint_timer.start()
        self.hide()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._apply_clickthrough()

    @Slot(object)
    def add_pending_comment(self, pending: PendingComment) -> None:
        if len(self.active_comments) >= self.max_active:
            self.active_comments = self.active_comments[-max(1, self.max_active // 2) :]

        if pending.style is CommentStyle.SCROLL:
            duration = self.overlay_config.scroll_duration_sec * pending.speed_multiplier
        else:
            duration = self.fixed_duration_sec

        self.active_comments.append(
            ActiveComment(
                text=pending.text,
                style=pending.style,
                color=pending.color,
                speed_multiplier=pending.speed_multiplier,
                lane=self._assign_lane(),
                created_monotonic=self.clock(),
                total_duration_sec=duration,
            )
        )
        self.update()

    @Slot()
    def clear_comments(self) -> None:
        self.active_comments.clear()
        self.last_used_lanes.clear()
        self.update()

    @Slot(bool)
    def set_overlay_visible(self, visible: bool) -> None:
        if visible:
            if QGuiApplication.platformName() == "offscreen":
                self.show()
            else:
                self.showFullScreen()
            self.raise_()
        else:
            self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self.active_comments:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        base_font = QFont("Yu Gothic UI", self.overlay_config.font_size)
        base_font.setBold(self.overlay_config.bold)
        metrics = QFontMetrics(base_font)
        now = self.clock()

        for comment in self.active_comments:
            elapsed = now - comment.created_monotonic
            progress = max(0.0, min(1.0, elapsed / max(0.001, comment.total_duration_sec)))
            if comment.style is CommentStyle.SCROLL:
                text_width = metrics.horizontalAdvance(comment.text)
                x = int(self.width() - progress * (self.width() + text_width + 80))
                y = int(comment.lane * self._lane_height() + self.overlay_config.top_margin)
                self._draw_text(painter, base_font, comment.text, x, y, comment.color.value, self.overlay_config.opacity)
            else:
                fixed_font = QFont(base_font)
                fixed_font.setPointSize(int(self.overlay_config.font_size * 1.5))
                fixed_metrics = QFontMetrics(fixed_font)
                text_width = fixed_metrics.horizontalAdvance(comment.text)
                x = int((self.width() - text_width) / 2)
                y = int(self.height() * 0.15 if comment.style is CommentStyle.TOP else self.height() * 0.85)
                opacity = self._fixed_opacity(progress) * self.overlay_config.opacity
                self._draw_text(painter, fixed_font, comment.text, x, y, comment.color.value, opacity)

        painter.end()

    def _tick(self) -> None:
        now = self.clock()
        self.active_comments = [
            comment
            for comment in self.active_comments
            if now - comment.created_monotonic <= comment.total_duration_sec
        ]
        self.update()

    def _draw_text(
        self,
        painter: QPainter,
        font: QFont,
        text: str,
        x: int,
        y: int,
        color_hex: str,
        opacity: float,
    ) -> None:
        painter.setFont(font)
        shadow = QColor(0, 0, 0, int(255 * max(0.0, min(1.0, opacity))))
        painter.setPen(QPen(shadow))
        for dx, dy in ((1, 1), (-1, -1), (1, -1), (-1, 1)):
            painter.drawText(x + dx, y + dy, text)

        color = QColor(color_hex)
        color.setAlphaF(max(0.0, min(1.0, opacity)))
        painter.setPen(QPen(color))
        painter.drawText(x, y, text)

    def _lane_height(self) -> int:
        return self.overlay_config.font_size + self.overlay_config.lane_padding

    def _lane_count(self) -> int:
        return max(1, int((self.height() - self.overlay_config.top_margin) / self._lane_height()))

    def _assign_lane(self) -> int:
        total = self._lane_count()
        active_lanes = {comment.lane for comment in self.active_comments if comment.style is CommentStyle.SCROLL}
        recent = set(self.last_used_lanes[-min(max(1, total // 3), 5) :])
        candidates = [lane for lane in range(total) if lane not in active_lanes and lane not in recent]
        if not candidates:
            candidates = [lane for lane in range(total) if lane not in active_lanes] or list(range(total))

        lane = candidates[0]
        self.last_used_lanes.append(lane)
        if len(self.last_used_lanes) > total:
            self.last_used_lanes = self.last_used_lanes[-total:]
        return lane

    def save_snapshot(self, path: Path) -> bool:
        return self.grab().save(str(path), "PNG")

    @staticmethod
    def _fixed_opacity(progress: float) -> float:
        if progress < 0.15:
            return progress / 0.15
        if progress > 0.8:
            return max(0.0, 1.0 - (progress - 0.8) / 0.2)
        return 1.0

    def _apply_clickthrough(self) -> None:
        hwnd = int(self.winId())
        styles = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd,
            GWL_EXSTYLE,
            styles | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW,
        )
