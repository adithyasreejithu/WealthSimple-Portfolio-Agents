"""Shared logging helper for the WealthSimple portfolio tools."""

from __future__ import annotations

import logging

from config import DATE_FORMAT, LOG_FORMAT, LOG_PATH



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
