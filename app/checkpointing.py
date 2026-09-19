"""Checkpointer selection: durable Redis when configured, in-memory otherwise.

`RedisSaver.from_conn_string` is a context manager whose saver is only valid
inside its block, so callers pass in an `ExitStack` that outlives the request
(the FastAPI lifespan owns one for the process).
"""
from __future__ import annotations

import logging
import os
from contextlib import ExitStack
from typing import Any
from urllib.parse import urlsplit

from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger(__name__)

REDIS = "redis"
MEMORY = "memory"
MEMORY_DEGRADED = "memory (degraded)"


def make_checkpointer(stack: ExitStack, redis_url: str | None = None) -> tuple[Any, str]:
    """Return `(saver, backend_name)`.

    A blank or unset `REDIS_URL` selects `MemorySaver` silently -- that is the
    documented zero-infrastructure path for demos and the test suite. A URL that
    is set but unusable warns loudly and degrades, because a production deploy
    that silently runs non-durable is the known risk of having a fallback here.
    """
    url = redis_url if redis_url is not None else os.environ.get("REDIS_URL", "")
    if not url:
        return MemorySaver(), MEMORY

    try:
        from langgraph.checkpoint.redis import RedisSaver

        saver = stack.enter_context(RedisSaver.from_conn_string(url))
        saver.setup()
        return saver, REDIS
    except Exception as exc:
        # Log only host:port, never the raw URL or exception body -- both can
        # carry the REDIS_URL's embedded credentials (redis://user:pass@host).
        parsed = urlsplit(url)
        safe_host = parsed.hostname or "?"
        if parsed.port:
            safe_host = f"{safe_host}:{parsed.port}"
        logger.warning(
            "REDIS_URL host %s is set but unusable (%s). Falling back to in-memory "
            "checkpoints -- graph state will NOT survive a restart. Redis Stack is "
            "required; plain Redis without RediSearch cannot back RedisSaver.",
            safe_host,
            type(exc).__name__,
        )
        return MemorySaver(), MEMORY_DEGRADED
