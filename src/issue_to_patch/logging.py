"""Structured logging.

Every log line carries the ``run_id`` of the run it belongs to, bound through a
context variable so nodes and tools do not have to thread it manually. This is
what makes the audit trail (Principle 2) reconstructable from logs alone.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

import structlog
from structlog.types import EventDict, WrappedLogger

_run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)

_configured = False


def _add_run_id(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    run_id = _run_id_var.get()
    if run_id is not None:
        event_dict.setdefault("run_id", run_id)
    return event_dict


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Idempotently configure structlog + stdlib logging."""
    global _configured
    if _configured:
        return

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_run_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(format="%(message)s", level=level.upper())
    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


@contextmanager
def bind_run_id(run_id: str) -> Iterator[None]:
    """Bind ``run_id`` for the duration of the block."""
    token = _run_id_var.set(run_id)
    try:
        yield
    finally:
        _run_id_var.reset(token)


def current_run_id() -> str | None:
    return _run_id_var.get()
