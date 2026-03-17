# utils/logger.py
"""
Centralized logger — safe for both local and cloud (Streamlit) environments.
On cloud, file logging is silently skipped if the logs/ dir is not writable.
"""
import sys
import os
from pathlib import Path
from loguru import logger


def setup_logger(level: str = "INFO") -> None:
    logger.remove()
    logger.add(
        sys.stdout,
        level=level,
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )
    # File logging — only if we have a writable directory
    try:
        log_dir = Path(__file__).parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        if os.access(log_dir, os.W_OK):
            logger.add(
                log_dir / "apex_{time:YYYY-MM-DD}.log",
                level="DEBUG",
                rotation="10 MB",
                retention="7 days",
                compression="zip",
                format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
                enqueue=True,
            )
    except Exception:
        pass  # Silently skip file logging on read-only cloud filesystems


setup_logger()
__all__ = ["logger"]
