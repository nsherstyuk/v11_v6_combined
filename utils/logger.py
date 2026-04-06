"""
Centralized logging configuration for the IBKR + Grok Swing Trading Agent.

Logs to both console (INFO+) and a rotating file in logs/ (DEBUG+).
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime


def setup_logger(
    name: str = "swing_agent",
    log_dir: str = "logs",
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    max_bytes: int = 5 * 1024 * 1024,  # 5 MB per file
    backup_count: int = 5,
) -> logging.Logger:
    """
    Create and return a configured logger instance.

    Args:
        name: Logger name.
        log_dir: Directory for log files.
        console_level: Minimum level for console output.
        file_level: Minimum level for file output.
        max_bytes: Max size of each log file before rotation.
        backup_count: Number of rotated log files to keep.

    Returns:
        Configured logging.Logger instance.
    """
    # Ensure log directory exists
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)  # Capture everything; handlers filter

    # Avoid adding duplicate handlers on re-import
    if logger.handlers:
        return logger

    # --- Log format ---
    fmt = "%(asctime)s | %(levelname)-8s | %(module)s:%(lineno)d | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=date_fmt)

    # --- Console handler ---
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # --- Rotating file handler ---
    log_filename = os.path.join(log_dir, f"swing_agent_{datetime.now():%Y%m%d}.log")
    file_handler = RotatingFileHandler(
        log_filename, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
