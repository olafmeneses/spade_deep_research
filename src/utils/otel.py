"""Small OpenTelemetry helpers for benchmark subprocesses."""

import logging
import os
from typing import MutableMapping

logger = logging.getLogger(__name__)


def with_otel_flush_defaults(env: MutableMapping[str, str]) -> MutableMapping[str, str]:
    env.setdefault("OTEL_BSP_SCHEDULE_DELAY", "1000")
    env.setdefault("OTEL_BSP_EXPORT_TIMEOUT", "10000")
    return env


def configure_otel_flush_defaults() -> None:
    """Configure shorter OpenTelemetry batch flush intervals for this process."""
    with_otel_flush_defaults(os.environ)


def force_flush_otel(timeout_millis: int = 10000) -> None:
    """Best-effort flush of OpenTelemetry spans before a short-lived process exits."""
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        force_flush = getattr(provider, "force_flush", None)
        if callable(force_flush):
            force_flush(timeout_millis=timeout_millis)
    except Exception:
        logger.debug("OpenTelemetry force flush failed", exc_info=True)
