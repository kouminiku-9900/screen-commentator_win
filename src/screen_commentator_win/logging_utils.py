from __future__ import annotations

import logging

from .paths import AppPaths


def configure_logging(paths: AppPaths) -> None:
    paths.ensure_directories()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(paths.app_log_file, encoding="utf-8")],
        force=True,
    )

