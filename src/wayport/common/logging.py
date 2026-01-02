"""Logging configuration using structlog."""

from __future__ import annotations

import logging
import sys
from typing import Literal

import structlog


def setup_logging(
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO",
    json_output: bool = False,
) -> None:
    """Configure structlog for the application.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        json_output: If True, output JSON format; otherwise pretty console output
    """
    # Set up standard logging to capture third-party logs
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=getattr(logging, level),
    )

    # Configure processors
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if json_output:
        # JSON output for production
        processors: list[structlog.types.Processor] = [
            *shared_processors,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Pretty console output for development
        processors = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a logger instance.

    Args:
        name: Optional name for the logger (typically __name__)

    Returns:
        A bound logger instance
    """
    return structlog.get_logger(name)
