# utils/logger.py
"""
Centralized logger configuration using loguru.
All modules import from here for consistent log formatting.
"""

import sys
from pathlib import Path
from loguru import logger

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def setup_logger(level: str = "DEBUG") -> None:
    """Configure loguru with console + rotating file sinks."""
    logger.remove()  # remove default handler

    # ── Console sink (INFO and above, colourised) ──────────────────────────
    logger.add(
        sys.stdout,
        level="INFO",
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )

    # ── File sink (DEBUG and above, rotating 10 MB, 7-day retention) ───────
    logger.add(
        LOG_DIR / "apex_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        enqueue=True,   # thread-safe async writing
    )


# Initialise on import
setup_logger()

__all__ = ["logger"]
