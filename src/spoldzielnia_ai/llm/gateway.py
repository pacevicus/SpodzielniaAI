"""LLM gateway (chunk 3.4, 7.2): one place that decides where a prompt may go.

Endpoints speak the standard OpenAI API (LM Studio, Ollama, vLLM …), reached through
LiteLLM's ``openai/`` provider with a configurable ``api_base``. Where the deployments come from
is a ``ConfigSource``: ``.env`` (``local-chat``, ``local-vlm``, ``local-embed`` and an optional
``cloud-chat``) or, since Stage 7, the admin's ACTIVE revision (``llm.config_store``), which a
process picks up without a restart. Each purpose has its own route.

Routing policy (AGENTS.md §3 rule 4, chunk 10.1):

- Purposes in ``PRIVATE_PURPOSES`` (PII scrubbing, private notes, promotion, vision,
  embeddings, speech-to-text, OCR, image embeddings) go only to deployments **cleared for
  private data**, whatever the caller passes. That is defence in depth against a missing
  ``private_data=True``.
- ``private_data=True`` → cleared deployments only. A deployment is cleared when an admin
  cleared its provider and its endpoint is on ``llm.private_data_endpoints``; locality alone no
  longer decides. If none is configured, or the call to it fails, ``ModelUnavailable`` is
  raised. **There is never a fallback to another deployment.**
- Cloud deployments are used only if ``llm.allow_cloud`` is set. The configuration source
  cannot widen this: a route to an uncleared deployment for a private call is refused here,
  whatever the database says (``llm_route_refused_total``).
- Cloud deployments go out through ``llm.egress_proxy_url`` (7.3) when it is set; ``local``
  only decides that network path.

Structured output: ``response_model`` (pydantic) is sent as a JSON schema, and the reply is
validated. An invalid reply gets one corrective retry, then ``LLMOutputInvalid``. With
``llm.structured_output = "prompt"`` the schema goes into the system prompt instead of
``response_format``: some local servers can't constrain output for some models (LM Studio's
grammar sampler fails on Bielik's chat template with a 400), and the validation and retry stay
the same.

Failures: connection errors, timeouts, 5xx and 408/429 are ``ModelUnavailable`` (worth
retrying later). Any other 4xx from the server is ``ModelRejected``: the same request
would fail again.
No prompt or completion is logged, and LiteLLM's callbacks, telemetry and cost-map download are
off.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict, cast, overload

from pydantic import BaseModel, ValidationError

from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.core.metrics import LLM_ERRORS, LLM_REQUEST_SECONDS, LLM_ROUTE_REFUSED
from spoldzielnia_ai.llm.endpoints import may_receive_private_data
from spoldzielnia_ai.llm.usage import UsageEvent
from spoldzielnia_ai.llm.usage_context import current_usage_owner

if TYPE_CHECKING:
    from collections.abc import Mapping

    from spoldzielnia_ai.core.settings import LlmSettings
    from spoldzielnia_ai.llm.transcription import Transcription
    from spoldzielnia_ai.llm.usage import UsageSink

# Before LiteLLM is imported anywhere: never download the model price map at runtime.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

log = get_logger(__name__)


class LLMPurpose(StrEnum):
    PII_SCRUB = "PII_SCRUB"
    PRIVATE_NOTE = "PRIVATE_NOTE"
    PROMOTION = "PROMOTION"
    VLM = "VLM"
    EMBED = "EMBED"
    RAG = "RAG"  # Stage 5: canonical (public) knowledge only
    TRANSCRIBE = "TRANSCRIBE"  # 10.4: speech-to-text of members' recordings
    OCR = "OCR"  # 10.5: text of scanned document pages (a vision model)
    IMAGE_EMBED = "IMAGE_EMBED"  # 10.6: atlas photos and recognition crops
    PLAYGROUND = "PLAYGROUND"  # 7.5: an admin's own test prompt, never retrieval or user data


PRIVATE_PURPOSES = frozenset(
    {
        LLMPurpose.PII_SCRUB,
        LLMPurpose.PRIVATE_NOTE,
        LLMPurpose.PROMOTION,
        LLMPurpose.VLM,
        LLMPurpose.EMBED,
        LLMPurpose.TRANSCRIBE,
        LLMPurpose.OCR,
        LLMPurpose.IMAGE_EMBED,
    }
)

LOCAL_CHAT, LOCAL_VLM, LOCAL_EMBED, LOCAL_TRANSCRIBE, LOCAL_IMAGE_EMBED, CLOUD_CHAT = (
    "local-chat",
    "local-vlm",
    "local-embed",
    "local-transcribe",
    "local-image-embed",
    "cloud-chat",
)


class LLMError(Exception):
    pass


class ModelUnavailable(LLMError):
    """No usable deployment for a purpose (for a private one: none cleared), or the call to it
    failed. Worth retrying later."""


class ModelNotConfigured(ModelUnavailable):
    """No usable model for ``purpose`` at all (none routed, none cleared for private data, or
    the cloud ceiling forbids it), as opposed to a configured model that is down (10.2).
    Callers turn the feature off or park the work until an admin configures one (P5);
    retrying soon won't help."""

    def __init__(self, purpose: LLMPurpose) -> None:
        super().__init__(f"no model configured for {purpose.value}")
        self.purpose = purpose


class ModelRejected(LLMError):
    """The model server refused the request (4xx): retrying the same request won't help."""


class LLMOutputInvalid(LLMError):
    """The model did not produce output matching the required schema (after one retry)."""


class TextPart(TypedDict):
    type: Literal["text"]
    text: str


class ImageUrl(TypedDict):
    url: str  # data: URL — images never leave the server as links


class ImagePart(TypedDict):
    type: Literal["image_url"]
    image_url: ImageUrl


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str | list[TextPart | ImagePart]


@dataclass(frozen=True, slots=True)
class LLMResult[T: BaseModel]:
    content: str
    parsed: T | None
    deployment: str
    model: str
    attempts: int = 1
    usage: dict[str, int] = field(default_factory=dict[str, int])


class LLMGateway(Protocol):
    @overload
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        purpose: LLMPurpose,
        private_data: bool = False,
        response_model: None = None,
    ) -> LLMResult[BaseModel]: ...

    @overload
    async def complete[T: BaseModel](
        self,
        messages: list[ChatMessage],
        *,
        purpose: LLMPurpose,
        private_data: bool = False,
        response_model: type[T],
    ) -> LLMResult[T]: ...

    async def embed(
        self,
        texts: list[str],
        *,
        private_data: bool = True,
        purpose: LLMPurpose = ...,
        deployment: Deployment | None = None,
    ) -> list[list[float]]: ...


class ChatTransport(Protocol):
    """What the gateway needs from LiteLLM (tests substitute a fake). ``model`` is the
    deployment name."""

    async def acompletion(self, model: str, messages: list[Any], **kwargs: Any) -> Any: ...

    async def aembedding(self, model: str, input: list[str], **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class Deployment:
    """One model on one endpoint. ``None`` parameters fall back to ``LlmSettings``.

    ``local`` decides the network path (direct or through the egress proxy). ``private_data``
    decides whether private purposes may use it (10.1); ``None`` (test shorthand) means
    "as local"."""

    name: str
    model: str
    base_url: str
    api_key: str | None
    local: bool
    private_data: bool | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    structured_output: Literal["json_schema", "prompt"] | None = None
    timeout_seconds: float | None = None
    provider: str = "openai"
    # Price per million tokens in micro-units (15.1); ``None``: unpriced, tokens only.
    price_in_micro: int | None = None
    price_out_micro: int | None = None

    @property
    def cleared(self) -> bool:
        return self.local if self.private_data is None else self.private_data


@dataclass(frozen=True, slots=True)
class ResolvedLlmConfig:
    """What a process routes by. ``revision`` 0 is ``.env``; otherwise the ACTIVE revision."""

    revision: int
    deployments: Mapping[str, Deployment]
    routes: Mapping[LLMPurpose, str]


class ConfigSource(Protocol):
    async def current(self) -> ResolvedLlmConfig: ...


@dataclass(frozen=True, slots=True)
class StaticConfig:
    config: ResolvedLlmConfig

    async def current(self) -> ResolvedLlmConfig:
        return self.config


def deployments(settings: LlmSettings) -> dict[str, Deployment]:
    # An empty secret file means "no token".
    key = settings.api_key.get_secret_value().strip() or None if settings.api_key else None
    found: dict[str, Deployment] = {}
    for name, model in (
        (LOCAL_CHAT, settings.chat_model),
        (LOCAL_VLM, settings.vlm_model),
        (LOCAL_EMBED, settings.embed_model),
        (LOCAL_TRANSCRIBE, settings.transcribe_model),
        (LOCAL_IMAGE_EMBED, settings.image_embed_model),
    ):
        if model:
            found[name] = Deployment(
                name,
                model,
                settings.local_base_url,
                key,
                local=True,
                private_data=may_receive_private_data(settings.local_base_url, settings),
            )
    if settings.allow_cloud and settings.cloud_model and settings.cloud_base_url:
        cloud_key = settings.cloud_api_key.get_secret_value() if settings.cloud_api_key else None
        found[CLOUD_CHAT] = Deployment(
            CLOUD_CHAT,
            settings.cloud_model,
            settings.cloud_base_url,
            cloud_key,
            local=False,
            # ``.env`` has no clearance step: its cloud model never gets private data.
            private_data=False,
        )
    return found


def _kind_deployment(purpose: LLMPurpose) -> str:
    if purpose in (LLMPurpose.VLM, LLMPurpose.OCR):
        return LOCAL_VLM
    if purpose == LLMPurpose.EMBED:
        return LOCAL_EMBED
    if purpose == LLMPurpose.TRANSCRIBE:
        return LOCAL_TRANSCRIBE
    if purpose == LLMPurpose.IMAGE_EMBED:
        return LOCAL_IMAGE_EMBED
    return LOCAL_CHAT


def env_config(settings: LlmSettings) -> ResolvedLlmConfig:
    """``.env`` as before Stage 7: one local model per kind; the cloud chat model only for
    purposes without private data, and only when there is no local chat model."""
    found = deployments(settings)
    routes: dict[LLMPurpose, str] = {}
    for purpose in LLMPurpose:
        if purpose == LLMPurpose.PLAYGROUND:
            continue
        wanted = _kind_deployment(purpose)
        if wanted in found:
            routes[purpose] = wanted
        elif wanted == LOCAL_CHAT and purpose not in PRIVATE_PURPOSES and CLOUD_CHAT in found:
            routes[purpose] = CLOUD_CHAT
    return ResolvedLlmConfig(0, found, routes)


_litellm_ready = False


def _configure_litellm() -> None:
    global _litellm_ready
    if _litellm_ready:
        return
    import litellm

    litellm.turn_off_message_logging = True
    # httpx (like the rest of the stack) instead of aiohttp.
    litellm.disable_aiohttp_transport = True
    litellm.suppress_debug_info = True
    litellm.success_callback = []
    litellm.failure_callback = []
    litellm.callbacks = []
    _litellm_ready = True


class LiteLLMTransport:
    """LiteLLM's ``acompletion``/``aembedding`` per deployment (OpenAI-compatible, one retry,
    no fallbacks). Not LiteLLM's Router: it manages its own HTTP clients, and cloud
    deployments need one that goes through the egress proxy (7.3)."""

    def __init__(self, config: ResolvedLlmConfig, settings: LlmSettings) -> None:
        _configure_litellm()
        self._config = config
        self._settings = settings
        self._clients: dict[str, Any] = {}

    def _client(self, deployment: Deployment, timeout: float) -> Any:
        """An OpenAI SDK client over the egress proxy, for cloud deployments only."""
        if deployment.name not in self._clients:
            import httpx
            from openai import AsyncOpenAI

            http = httpx.AsyncClient(
                proxy=self._settings.egress_proxy_url, trust_env=False, timeout=timeout
            )
            self._clients[deployment.name] = AsyncOpenAI(
                api_key=deployment.api_key or "not-needed",
                base_url=deployment.base_url,
                http_client=http,
                max_retries=1,
                timeout=timeout,
            )
        return self._clients[deployment.name]

    def _params(self, name: str) -> dict[str, Any]:
        deployment = self._config.deployments[name]
        timeout = deployment.timeout_seconds or self._settings.timeout_seconds
        params: dict[str, Any] = {
            "model": f"{deployment.provider}/{deployment.model}",
            "api_base": deployment.base_url,
            "api_key": deployment.api_key or "not-needed",
            "timeout": timeout,
            "max_retries": 1,
        }
        if not deployment.local and self._settings.egress_proxy_url:
            params["client"] = self._client(deployment, timeout)
        return params

    async def acompletion(self, model: str, messages: list[Any], **kwargs: Any) -> Any:
        import litellm

        return await litellm.acompletion(messages=messages, **self._params(model), **kwargs)

    async def aembedding(self, model: str, input: list[str], **kwargs: Any) -> Any:
        import litellm

        return await litellm.aembedding(input=input, **self._params(model), **kwargs)


def _json_schema(model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model.__name__,
            "schema": model.model_json_schema(),
            "strict": True,
        },
    }


_FENCED = re.compile(r"```(?:json)?[ \t]*\n(.*?)\n?```", re.DOTALL)


def _extract_json(text: str) -> str:
    """Local models often wrap JSON in ```json fences or add a sentence around it (always
    without a schema constraint). Take the fenced block, else the outermost ``{…}``."""
    stripped = text.strip()
    if fenced := _FENCED.search(stripped):
        return fenced.group(1).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start > 0 and end > start:
        return stripped[start : end + 1]
    return stripped


_EXAMPLE_SCALARS: dict[str, Any] = {"string": "…", "integer": 0, "number": 0, "boolean": False}


def _example(schema: dict[str, Any], defs: dict[str, Any]) -> Any:
    """A filled-in instance of ``schema``: models copy an example's shape faithfully, while a
    raw JSON Schema often comes back echoed (``{"properties": {...}, "type": "object"}``)."""
    if "$ref" in schema:
        return _example(defs[schema["$ref"].split("/")[-1]], defs)
    if "anyOf" in schema:
        options = [o for o in schema["anyOf"] if o.get("type") != "null"]
        return _example(options[0], defs) if options else None
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        properties: dict[str, Any] = schema.get("properties", {})
        return {name: _example(sub, defs) for name, sub in properties.items()}
    if kind == "array":
        return [_example(schema.get("items", {}), defs)]
    return _EXAMPLE_SCALARS.get(str(kind), "…")


def _field_notes(schema: dict[str, Any]) -> str:
    required = set(schema.get("required", []))
    lines: list[str] = []
    for name, sub in schema.get("properties", {}).items():
        note = sub.get("description") or sub.get("title") or ""
        flag = "wymagane" if name in required else "opcjonalne"
        lines.append(f"- {name} ({flag}){': ' + note if note else ''}")
    return "\n".join(lines)


def _schema_echo(text: str, model: type[BaseModel]) -> BaseModel | None:
    """A reply that copied the JSON Schema's shape (``properties`` holding the actual values
    next to ``type``/``title``): validate what is inside ``properties``."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    shell = cast("dict[str, Any]", data)
    if not isinstance(shell.get("properties"), dict) or not (
        {"type", "title", "required"} & shell.keys()
    ):
        return None
    try:
        return model.model_validate(shell["properties"])
    except ValidationError:
        return None


def _schema_in_prompt(messages: list[ChatMessage], model: type[BaseModel]) -> list[ChatMessage]:
    """``structured_output = "prompt"``: describe the answer in the (first) system message, as
    an example object plus field notes (not the raw JSON Schema, which models tend to echo).
    One system message only, as some chat templates reject a second one."""
    schema = model.model_json_schema()
    example = json.dumps(_example(schema, schema.get("$defs", {})), ensure_ascii=False)
    note = (
        "\n\nOdpowiedz wyłącznie jednym obiektem JSON o dokładnie takiej budowie (podstaw "
        f"własne wartości):\n{example}\nPola:\n{_field_notes(schema)}"
    )
    out = list(messages)
    first = out[0] if out else None
    if first is not None and first["role"] == "system" and isinstance(first["content"], str):
        out[0] = {"role": "system", "content": first["content"] + note}
    else:
        out.insert(0, {"role": "system", "content": note.strip()})
    return out


def _rejected(exc: Exception) -> bool:
    """A 4xx other than 408/429: the server understood and refused the request."""
    code = getattr(exc, "status_code", None)
    return isinstance(code, int) and 400 <= code < 500 and code not in (408, 429)


class LiteLLMGateway:
    """``transport`` fixes the transport (tests); otherwise one ``LiteLLMTransport`` per
    configuration revision is built on first use. Calls in flight finish on the transport they
    started with; the next call after a change uses the new one."""

    def __init__(
        self,
        settings: LlmSettings,
        transport: ChatTransport | None = None,
        *,
        source: ConfigSource | None = None,
        usage: UsageSink | None = None,
    ) -> None:
        self._settings = settings
        self._config = env_config(settings)
        self._source: ConfigSource = source or StaticConfig(self._config)
        self._usage = usage  # 15.2: every successful response is counted, when wired
        self._fixed_transport = transport
        self._transport: ChatTransport | None = transport
        self._transport_config: ResolvedLlmConfig | None = None
        self._pinned_transports: dict[Deployment, ChatTransport] = {}

    @property
    def config(self) -> ResolvedLlmConfig:
        """The configuration of the last call (``.env`` until the first one)."""
        return self._config

    async def refresh(self) -> ResolvedLlmConfig:
        self._config = await self._source.current()
        return self._config

    def _router(self) -> ChatTransport:
        if self._fixed_transport is not None:
            return self._fixed_transport
        if self._transport is None or self._transport_config is not self._config:
            self._transport = LiteLLMTransport(self._config, self._settings)
            self._transport_config = self._config
        return self._transport

    def configured(self, purpose: LLMPurpose, private_data: bool = False) -> bool:
        """Whether ``purpose`` has a usable deployment in the current configuration (no call,
        no metrics). ``refresh()`` first for an up-to-date answer."""
        private = private_data or purpose in PRIVATE_PURPOSES
        name = self._config.routes.get(purpose)
        deployment = self._config.deployments.get(name) if name is not None else None
        if deployment is None or (not deployment.local and not self._settings.allow_cloud):
            return False
        return deployment.cleared or not private

    def route(self, purpose: LLMPurpose, private_data: bool) -> Deployment:
        name = self._config.routes.get(purpose)
        deployment = self._config.deployments.get(name) if name is not None else None
        return self._allowed(deployment, purpose, private_data)

    def _allowed(
        self, deployment: Deployment | None, purpose: LLMPurpose, private_data: bool
    ) -> Deployment:
        """``deployment`` if policy lets ``purpose`` use it, else ``ModelNotConfigured``."""
        private = private_data or purpose in PRIVATE_PURPOSES
        if deployment is not None and not deployment.local and not self._settings.allow_cloud:
            deployment = None  # the operator's ceiling (S2), whatever the configuration says
        if deployment is not None and private and not deployment.cleared:
            # Unreachable through the admin service and the database triggers (P1) unless the
            # provider was un-cleared after activation, or the configuration was tampered with.
            # Refuse, never fall back.
            LLM_ROUTE_REFUSED.labels(purpose=purpose.value).inc()
            log.error(
                "llm.route_refused",
                purpose=purpose.value,
                deployment=deployment.name,
                revision=self._config.revision,
            )
            deployment = None
        if deployment is None:
            raise ModelNotConfigured(purpose)
        return deployment

    async def _meter(
        self, purpose: LLMPurpose, deployment: Deployment, usage: Mapping[str, int]
    ) -> None:
        """Count one successful response for the current owner (15.2). Endpoints that report
        no tokens (speech, image embeddings) count the call with zero tokens."""
        if self._usage is None:
            return
        await self._usage.record(
            UsageEvent(
                user_id=current_usage_owner(),
                purpose=purpose.value,
                model=deployment.name,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                price_in_micro=deployment.price_in_micro,
                price_out_micro=deployment.price_out_micro,
            )
        )

    def _pinned(self, deployment: Deployment) -> ChatTransport:
        """A transport for a deployment outside the current configuration (an index
        generation's embedding model, 10.3), cached per deployment."""
        if self._fixed_transport is not None:
            return self._fixed_transport
        if deployment not in self._pinned_transports:
            config = ResolvedLlmConfig(-1, {deployment.name: deployment}, {})
            self._pinned_transports[deployment] = LiteLLMTransport(config, self._settings)
        return self._pinned_transports[deployment]

    @overload
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        purpose: LLMPurpose,
        private_data: bool = False,
        response_model: None = None,
    ) -> LLMResult[BaseModel]: ...

    @overload
    async def complete[T: BaseModel](
        self,
        messages: list[ChatMessage],
        *,
        purpose: LLMPurpose,
        private_data: bool = False,
        response_model: type[T],
    ) -> LLMResult[T]: ...

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        purpose: LLMPurpose,
        private_data: bool = False,
        response_model: type[BaseModel] | None = None,
    ) -> LLMResult[Any]:
        await self.refresh()
        deployment = self.route(purpose, private_data)
        settings = self._settings
        kwargs: dict[str, Any] = {
            "max_tokens": deployment.max_tokens or settings.max_tokens,
            "temperature": deployment.temperature
            if deployment.temperature is not None
            else settings.temperature,
        }
        structured = deployment.structured_output or settings.structured_output
        if response_model is not None and structured == "prompt":
            messages = _schema_in_prompt(messages, response_model)
        elif response_model is not None:
            kwargs["response_format"] = _json_schema(response_model)
        conversation: list[Any] = list(messages)
        router = self._router()
        for attempt in (1, 2):
            started = time.monotonic()
            try:
                response = await router.acompletion(deployment.name, conversation, **kwargs)
            except Exception as exc:  # connection refused, timeout, 5xx …
                rejected = _rejected(exc)
                LLM_ERRORS.labels(
                    deployment=deployment.name, kind="rejected" if rejected else "unavailable"
                ).inc()
                log.warning(
                    "llm.call_failed",
                    deployment=deployment.name,
                    error=type(exc).__name__,
                    status=getattr(exc, "status_code", None),
                    hint="try llm.structured_output=prompt"
                    if rejected and "response_format" in kwargs
                    else None,
                )
                if rejected:
                    raise ModelRejected(f"{deployment.name}: {type(exc).__name__}") from exc
                raise ModelUnavailable(f"{deployment.name}: {type(exc).__name__}") from exc
            LLM_REQUEST_SECONDS.labels(deployment=deployment.name, purpose=purpose.value).observe(
                time.monotonic() - started
            )
            content = str(response.choices[0].message.content or "")
            usage = _usage(response)
            await self._meter(purpose, deployment, usage)
            if response_model is None:
                return LLMResult(content, None, deployment.name, deployment.model, attempt, usage)
            try:
                parsed = response_model.model_validate_json(_extract_json(content))
            except ValidationError as exc:
                echoed = _schema_echo(_extract_json(content), response_model)
                if echoed is not None:
                    log.info("llm.schema_echo_recovered", deployment=deployment.name)
                    return LLMResult(
                        content, echoed, deployment.name, deployment.model, attempt, usage
                    )
                log.warning("llm.invalid_output", deployment=deployment.name, attempt=attempt)
                LLM_ERRORS.labels(deployment=deployment.name, kind="invalid_output").inc()
                conversation = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": "Odpowiedź nie spełnia schematu JSON. Zwróć wyłącznie poprawny "
                        f"JSON zgodny ze schematem. Błędy: {_errors(exc)}",
                    },
                ]
                continue
            return LLMResult(content, parsed, deployment.name, deployment.model, attempt, usage)
        schema = response_model.__name__ if response_model is not None else "text"
        raise LLMOutputInvalid(f"{deployment.name}: output does not match {schema}")

    async def embed(
        self,
        texts: list[str],
        *,
        private_data: bool = True,
        purpose: LLMPurpose = LLMPurpose.EMBED,
        deployment: Deployment | None = None,
    ) -> list[list[float]]:
        """``purpose`` is EMBED except for probes (PLAYGROUND: synthetic text only).

        ``deployment`` pins the model instead of routing ``purpose`` (10.3): an index
        generation is searched and written with the model that built it, which after a model
        change is no longer the routed one. The same policy applies to it as to a route."""
        await self.refresh()
        if deployment is None:
            deployment = self.route(purpose, private_data)
            router = self._router()
        else:
            deployment = self._allowed(deployment, purpose, private_data)
            router = self._pinned(deployment)
        started = time.monotonic()
        try:
            response = await router.aembedding(deployment.name, input=texts)
        except Exception as exc:
            LLM_ERRORS.labels(deployment=deployment.name, kind="unavailable").inc()
            raise ModelUnavailable(f"{deployment.name}: {type(exc).__name__}") from exc
        LLM_REQUEST_SECONDS.labels(deployment=deployment.name, purpose=purpose.value).observe(
            time.monotonic() - started
        )
        await self._meter(purpose, deployment, _usage(response))
        return [list(map(float, item["embedding"])) for item in response.data]

    async def embed_images(
        self,
        images: list[str],
        *,
        private_data: bool = True,
        purpose: LLMPurpose = LLMPurpose.IMAGE_EMBED,
        deployment: Deployment | None = None,
    ) -> list[list[float]]:
        """Image embeddings (10.6) of inline ``data:`` URIs, L2-normalised. ``deployment``
        pins the model of an atlas generation (P8), with the same policy as a route;
        ``purpose`` is PLAYGROUND only for probes (synthetic images)."""
        from spoldzielnia_ai.llm.image_embedding import post_image_embeddings

        await self.refresh()
        if deployment is None:
            deployment = self.route(purpose, private_data)
        else:
            deployment = self._allowed(deployment, purpose, private_data)
        started = time.monotonic()
        try:
            vectors = await post_image_embeddings(deployment, images, settings=self._settings)
        except (ModelRejected, ModelUnavailable, LLMOutputInvalid) as exc:
            kind = (
                "rejected"
                if isinstance(exc, ModelRejected)
                else "invalid_output"
                if isinstance(exc, LLMOutputInvalid)
                else "unavailable"
            )
            LLM_ERRORS.labels(deployment=deployment.name, kind=kind).inc()
            raise
        LLM_REQUEST_SECONDS.labels(deployment=deployment.name, purpose=purpose.value).observe(
            time.monotonic() - started
        )
        await self._meter(purpose, deployment, {})  # the endpoint reports no tokens
        return vectors

    async def transcribe(
        self,
        audio: bytes,
        *,
        filename: str = "audio.wav",
        language: str | None = None,
        private_data: bool = True,
        purpose: LLMPurpose = LLMPurpose.TRANSCRIBE,
    ) -> Transcription:
        """Speech-to-text of one audio segment (10.4). ``purpose`` is TRANSCRIBE except for
        probes (PLAYGROUND: a synthetic clip only). TRANSCRIBE is private: only a deployment
        cleared for private data receives members' recordings, never another one (P1, P9)."""
        from spoldzielnia_ai.llm.transcription import post_transcription

        await self.refresh()
        deployment = self.route(purpose, private_data)
        started = time.monotonic()
        try:
            result = await post_transcription(
                deployment,
                audio,
                filename,
                language=language or self._settings.transcribe_language,
                settings=self._settings,
            )
        except (ModelRejected, ModelUnavailable) as exc:
            kind = "rejected" if isinstance(exc, ModelRejected) else "unavailable"
            LLM_ERRORS.labels(deployment=deployment.name, kind=kind).inc()
            log.warning("llm.transcription_failed", deployment=deployment.name, error=str(exc))
            raise
        except LLMOutputInvalid:
            LLM_ERRORS.labels(deployment=deployment.name, kind="invalid_output").inc()
            raise
        LLM_REQUEST_SECONDS.labels(deployment=deployment.name, purpose=purpose.value).observe(
            time.monotonic() - started
        )
        await self._meter(purpose, deployment, {})  # the endpoint reports no tokens
        return result


def _usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        key: int(getattr(usage, key, 0) or 0)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }


def _errors(exc: ValidationError) -> str:
    """Error locations and types only (no input values echoed back into the prompt)."""
    return json.dumps([{"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()][:10])
