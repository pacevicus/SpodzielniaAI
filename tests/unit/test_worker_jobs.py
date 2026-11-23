"""Chunk 2.5: only identifiers cross the queue; routing and Celery configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from pydantic import ValidationError

from spoldzielnia_ai.db.enums import DerivativeKind
from spoldzielnia_ai.files.validation import ALLOWED
from spoldzielnia_ai.notebook.queue import derivative_kinds
from spoldzielnia_ai.workers.celery_adapter import make_celery, register, send
from spoldzielnia_ai.workers.jobs import (
    KIND_TASK,
    NO_MEDIA_TASK_KINDS,
    TASK_QUEUE,
    DerivativeJob,
    Queue,
    TaskName,
)

if TYPE_CHECKING:
    from pathlib import Path

IDS = {"derivative_id": str(UUID(int=1)), "owner_id": str(UUID(int=2))}


@pytest.mark.parametrize(
    "message",
    [
        {**IDS, "content": "Treść notatki z PESEL 44051401359"},  # content smuggled along
        {**IDS, "body": b"%PDF-1.4"},
        {"derivative_id": "Treść notatki", "owner_id": IDS["owner_id"]},
        {"derivative_id": IDS["derivative_id"]},
        {},
    ],
    ids=["extra-content", "extra-bytes", "content-as-id", "missing-owner", "empty"],
)
def test_job_accepts_identifiers_only(message: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DerivativeJob.model_validate(message)


def test_job_round_trips_as_json_safe_message() -> None:
    job = DerivativeJob.model_validate(IDS)
    assert job.to_message() == IDS
    assert DerivativeJob.model_validate(job.to_message()) == job


def test_registered_task_rejects_content_at_runtime() -> None:
    app = make_celery("memory://", eager=True)
    seen: list[DerivativeJob] = []

    def run(job: DerivativeJob, attempt: int) -> bool:
        seen.append(job)
        return False

    register(app, TaskName.PARSE_DOCUMENT, run)
    send(app, TaskName.PARSE_DOCUMENT, DerivativeJob.model_validate(IDS))
    assert seen == [DerivativeJob.model_validate(IDS)]

    with pytest.raises(ValidationError):
        app.tasks[TaskName.PARSE_DOCUMENT.value].apply(kwargs={**IDS, "content": "secret"})
    assert len(seen) == 1


def test_retry_stops_after_max_retries() -> None:
    app = make_celery("memory://", eager=True)
    attempts: list[int] = []

    def run(job: DerivativeJob, attempt: int) -> bool:
        attempts.append(attempt)
        return True  # always "transient"

    register(app, TaskName.TRANSCRIBE_AUDIO, run, max_retries=2)
    app.tasks[TaskName.TRANSCRIBE_AUDIO.value].apply(kwargs=IDS)
    assert attempts == [0, 1, 2]


def test_celery_is_json_only_with_late_acks() -> None:
    conf = make_celery("redis://localhost:6379/1").conf
    assert conf.task_serializer == "json"
    assert conf.accept_content == ["json"]
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_ignore_result is True
    routes = conf.task_routes
    assert routes[TaskName.FETCH_LINK.value] == {"queue": "egress.fetch"}
    assert routes[TaskName.TRANSCRIBE_AUDIO.value] == {"queue": "media.transcribe"}


def test_every_kind_has_a_task_and_queue() -> None:
    assert set(KIND_TASK) == set(DerivativeKind) - NO_MEDIA_TASK_KINDS
    assert set(TASK_QUEUE) == set(TaskName)
    content_tasks = set(KIND_TASK.values()) - {TaskName.FETCH_LINK}
    assert {TASK_QUEUE[t] for t in content_tasks} == {Queue.TRANSCRIBE, Queue.VISION, Queue.PARSE}
    assert TASK_QUEUE[TaskName.FETCH_LINK] == Queue.FETCH  # the only egress task
    assert TASK_QUEUE[TaskName.SCAN_DERIVATIVE] == Queue.CORE_SCAN


@pytest.mark.parametrize(
    ("mime", "kinds"),
    [
        ("audio/mpeg", [DerivativeKind.TRANSCRIPT]),
        ("video/mp4", [DerivativeKind.TRANSCRIPT]),
        ("image/png", [DerivativeKind.IMAGE_DESCRIPTION]),
        ("application/pdf", [DerivativeKind.PARSED_DOC]),
        ("text/plain", [DerivativeKind.PARSED_DOC]),
        ("application/zip", []),
    ],
)
def test_derivative_kinds_per_file_type(mime: str, kinds: list[DerivativeKind]) -> None:
    assert derivative_kinds(ALLOWED[mime]) == kinds


def test_atlas_preparation_runs_in_worker_core(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic import SecretStr

    from spoldzielnia_ai.core.settings import (
        DbSettings,
        RedisSettings,
        WorkerSecuritySettings,
        WorkerSettings,
    )
    from spoldzielnia_ai.workers.jobs import JOB_MODEL, AtlasJob
    from spoldzielnia_ai.workers.runtime import WorkerRuntime

    assert TASK_QUEUE[TaskName.PREPARE_ATLAS] == Queue.PROMOTION  # worker-core consumes it
    assert JOB_MODEL[TaskName.PREPARE_ATLAS] is AtlasJob
    settings = WorkerSettings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        _secrets_dir=tmp_path,  # pyright: ignore[reportCallIssue]
        db=DbSettings(app_password=SecretStr("pw")),
        redis=RedisSettings(password=SecretStr("pw")),
        security=WorkerSecuritySettings(
            kek=SecretStr("MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")
        ),
    )
    runtime = WorkerRuntime(settings, make_celery("memory://", eager=True))
    routed: list[object] = []

    def prepare(job: object, _attempt: int) -> bool:
        routed.append(job)
        return True

    # The handler wraps the job (owner and usage flush), so check the routing by calling it.
    monkeypatch.setattr(runtime, "prepare_atlas", prepare)
    job = object()
    assert runtime.handler(TaskName.PREPARE_ATLAS)(job, 1) is True
    assert routed == [job]
