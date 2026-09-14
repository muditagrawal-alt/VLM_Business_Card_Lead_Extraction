"""Structured JSON logging.

Production emits one JSON object per line (ready for CloudWatch / `docker logs`);
development emits colourised key-value lines. Request, job and task ids are bound
to the logger so a single card can be traced end to end.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(level: str = "INFO", *, json_output: bool = False) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)

    processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        # A stdlib-backed factory, not PrintLoggerFactory: the stdlib
        # processors above read `logger.name`, which a PrintLogger does not
        # have. Pairing them raised AttributeError from inside the global
        # exception handler, turning a handled 500 into an unhandled one.
        # This also routes uvicorn and SQLAlchemy through the same handler.
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Route stdlib loggers (uvicorn, sqlalchemy) through the same handler.
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level)
    for noisy in ("uvicorn.access", "sqlalchemy.engine.Engine"):
        logging.getLogger(noisy).setLevel(max(log_level, logging.WARNING))


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
