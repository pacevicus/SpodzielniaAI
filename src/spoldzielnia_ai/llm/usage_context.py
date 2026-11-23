"""Who an LLM call is made for (stage 15, US-09), carried by the context rather than by every
call site.

The gateway is called from about fifteen places, most of them deep inside a request or a worker
job. Instead of passing a user id through all of them, the caller's identity is set once per
request (``auth.deps.current_user``) or per worker job (``workers.runtime``), and the gateway
reads it when it records a call. Work with no owner (scheduled maintenance, CLI jobs) is
recorded as "system".

Two carriers, because the two callers differ:

- a request runs in its own asyncio task, so a ``ContextVar`` set by a dependency is seen by
  everything that task awaits and never leaks into another request;
- a worker job is driven from a synchronous handler, and its engines run in helper threads
  (``asyncio.to_thread``) and in their own loops. A ``ContextVar`` does not reach all of them,
  so the job's owner is a process-wide value. A worker process runs one job at a time, so this
  is the job's owner for every call it makes.

Pure module: no database or HTTP imports, so any layer can use it.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 - used at runtime in annotations of the ContextVar

if TYPE_CHECKING:
    from collections.abc import Generator

_owner: ContextVar[UUID | None] = ContextVar("llm_usage_owner", default=None)
_job_owner: UUID | None = None


def current_usage_owner() -> UUID | None:
    """The user the current call is made for, or ``None`` for system work."""
    owner = _owner.get()
    return owner if owner is not None else _job_owner


def set_usage_owner(user_id: UUID | None) -> None:
    """Attribute the rest of the current asyncio task to ``user_id`` (request dependencies)."""
    _owner.set(user_id)


@contextmanager
def usage_owner(user_id: UUID | None) -> Generator[None]:
    """Attribute every call made in the block to ``user_id`` (worker jobs, one at a time per
    process), then restore the previous owner."""
    global _job_owner
    previous = _job_owner
    _job_owner = user_id
    try:
        yield
    finally:
        _job_owner = previous
