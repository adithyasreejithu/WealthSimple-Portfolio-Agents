"""Shared logging helper for the WealthSimple portfolio tools."""

from __future__ import annotations

import logging
from pathlib import Path


LOG_PATH = Path(__file__).resolve().parents[1] / "logs" / "SystemLogs.txt"
LOG_FORMAT = (
    "%(asctime)s.%(msecs)03d | %(levelname)-8s | pid=%(process)d | "
    "%(name)s:%(funcName)s:%(lineno)d | %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str) -> logging.Logger:
    """Return a file-backed logger with a consistent format."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    logger.setLevel(logging.DEBUG)

    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

    logger.addHandler(file_handler)
    logger.propagate = False
    return logger
