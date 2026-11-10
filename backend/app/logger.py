"""Logging setup for AndroidFileExplorer backend."""

import logging
import sys

from loguru import logger


class _InterceptHandler(logging.Handler):
    """Route stdlib logging records into loguru."""

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - glue code
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(exception=record.exc_info).log(level, record.getMessage())


def setup_logging(level: str = "INFO") -> None:
    """Configure loguru sinks and the stdlib logging bridge."""
    logger.remove()
    logger.add(sys.stderr, level=level, backtrace=False, diagnose=False)

    logging.root.handlers = [_InterceptHandler()]
    logging.root.setLevel(level)
    for name in list(logging.root.manager.loggerDict.keys()):
        std_logger = logging.getLogger(name)
        std_logger.handlers = []
        std_logger.propagate = True
