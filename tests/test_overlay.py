from __future__ import annotations

from screen_commentator_win.models import CommentColor, CommentStyle, OverlayConfig, PendingComment
from screen_commentator_win.overlay import OverlayWindow


def test_overlay_assigns_distinct_lanes_for_active_scroll_comments(qtbot) -> None:
    overlay = OverlayWindow(
        overlay_config=OverlayConfig(font_size=20, lane_padding=4),
        fixed_duration_sec=4.0,
        max_active=10,
    )
    overlay.resize(800, 600)
    qtbot.addWidget(overlay)

    for text in ("a", "b", "c"):
        overlay.add_pending_comment(
            PendingComment(
                text=text,
                style=CommentStyle.SCROLL,
                color=CommentColor.WHITE,
                speed_multiplier=1.0,
            )
        )

    assert [comment.lane for comment in overlay.active_comments] == [0, 1, 2]
