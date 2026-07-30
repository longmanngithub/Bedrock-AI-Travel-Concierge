"""Provider-agnostic LLM factory.

Every agent, the baseline, and the evaluation judge get their model through
here, so switching provider (Gemini -> Groq -> Ollama) is a one-line change in
`.env` with no code edits. CrewAI's `LLM` wraps LiteLLM, which understands the
`provider/model` naming convention.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING

from .config import get_settings
from .errors import AppError, ErrorCode

if TYPE_CHECKING:  # `from crewai import LLM` pulls a heavy chain — keep it out of
    from crewai import LLM  # the API process; imported lazily inside build_llm.


def _export_provider_keys() -> None:
    """Make sure provider keys are visible to LiteLLM via the environment."""
    settings = get_settings()
    if settings.google_api_key:
        os.environ.setdefault("GOOGLE_API_KEY", settings.google_api_key)
        # LiteLLM also reads GEMINI_API_KEY for the gemini/ prefix.
        os.environ.setdefault("GEMINI_API_KEY", settings.google_api_key)
    if settings.groq_api_key:
        os.environ.setdefault("GROQ_API_KEY", settings.groq_api_key)
    if settings.ollama_api_base:
        os.environ.setdefault("OLLAMA_API_BASE", settings.ollama_api_base)
    # Vertex AI (ADC-authenticated). LiteLLM reads these for `vertex_ai/` models.
    if settings.vertex_project:
        os.environ.setdefault("VERTEXAI_PROJECT", settings.vertex_project)
        os.environ.setdefault("VERTEXAI_LOCATION", settings.vertex_location)
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.vertex_project)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", settings.vertex_location)


def _vertex_quota_headers() -> dict[str, str]:
    """The `x-goog-user-project` header Vertex needs under *user* credentials.

    Google requires a billing/quota project on every Vertex call made with an
    `authorized_user` credential — what `gcloud auth application-default login`
    produces, and what DEPLOYMENT.md tells developers to use locally. The
    google-auth transport normally attaches it from the credential's own
    `quota_project_id`, but LiteLLM's Gemini path builds its request by hand
    with a bare `Authorization: Bearer` token and only ever sets this header on
    its text-to-speech route — so the header is dropped and every
    `generateContent` call comes back 404 ("not found or your project does not
    have access to it"), regardless of the model id. Verified against the raw
    REST endpoint: identical request, 404 without the header and 200 with it.

    Deliberately scoped to user credentials. A service-account credential (how
    deployed environments authenticate) needs no quota project, and sending one
    would newly require `serviceusage.services.use` on that project — so
    passing it unconditionally could break a working deployment. Returns {} on
    anything that isn't a user credential, including when google-auth or ADC is
    unavailable.
    """
    settings = get_settings()
    if not settings.vertex_project:
        return {}
    try:
        import google.auth

        credentials, _ = google.auth.default()
    except Exception:  # noqa: BLE001 - no ADC configured; nothing to add
        return {}
    # google.oauth2.credentials.Credentials is the user ("authorized_user")
    # flavour; service accounts are a different class entirely.
    if type(credentials).__module__.startswith("google.oauth2.credentials"):
        return {"x-goog-user-project": settings.vertex_project}
    return {}


def build_llm(model: str | None = None, temperature: float = 0.4, *, task: str | None = None) -> LLM:
    """Return a CrewAI LLM for the given (or default) model id.

    CrewAI 1.x routes `gemini/` models through Google's native SDK (installed
    via the `google-genai` dependency), and other providers such as
    `groq/` and `ollama/` through LiteLLM. Either way, switching provider is a
    one-line change in `.env` with no code edits here.

    Model resolution order: an explicit `model` always wins; otherwise if a
    `task` is given the model comes from the tier mapping (see routing.py);
    otherwise the legacy single `settings.model`. This is the one entry point —
    all tiering flows through here rather than a second factory.
    """
    from crewai import LLM  # lazy: keeps crewai/torch out of the API import graph

    _export_provider_keys()
    settings = get_settings()
    if model is None and task is not None:
        from .routing import model_for  # local import: routing imports config, avoid cycles

        model = model_for(task)
    kwargs: dict = {"model": model or settings.model, "temperature": temperature}
    # A request timeout stops a single stalled call from hanging the whole run;
    # num_retries lets LiteLLM recover from a transient blip before the error
    # bubbles up to the crew-level retry. Both are no-ops on providers that
    # don't honour them, so they're safe to always pass.
    if settings.request_timeout:
        kwargs["timeout"] = settings.request_timeout
    if settings.llm_num_retries:
        kwargs["additional_params"] = {"num_retries": settings.llm_num_retries}
    if (kwargs["model"] or "").startswith("vertex_ai/"):
        quota_headers = _vertex_quota_headers()
        if quota_headers:
            kwargs["extra_headers"] = quota_headers
    return LLM(**kwargs)


def build_judge_llm(temperature: float = 0.0) -> LLM:
    """Deterministic LLM used by the evaluation harness for grading."""
    return build_llm(task="judge", temperature=temperature)


def stream_call(
    prompt: str | list[dict], temperature: float = 0.7, *, task: str = "reply"
) -> Iterator[str]:
    """Yield the model's reply token-by-token as it is generated.

    Used by the conversational front door so the chat UI can render the
    concierge's reply as it streams, rather than waiting for the whole thing.
    Goes straight through LiteLLM's streaming API (which understands the same
    `provider/model` naming as CrewAI's `LLM`) so it works for the active
    `vertex_ai/` model as well as the `gemini/`, `groq/`, `ollama/` fallbacks.

    `prompt` may be a bare string (wrapped into a single user-role message,
    kept for callers that don't need a system/user split) or an already-built
    `[{"role": ..., "content": ...}, ...]` list — see conversation.py's
    `build_reply_messages` for why callers should prefer a real system/user
    split over one concatenated string.

    Yields nothing (rather than raising) on a provider error — the caller is
    expected to fall back to a non-streamed reply so a streaming hiccup never
    breaks a turn.
    """
    _export_provider_keys()
    settings = get_settings()
    from .routing import model_for  # local import to avoid an import cycle

    model = model_for(task)
    try:
        import litellm

        messages = [{"role": "user", "content": prompt}] if isinstance(prompt, str) else prompt
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if settings.request_timeout:
            kwargs["timeout"] = settings.request_timeout
        if settings.vertex_project and model.startswith("vertex_ai/"):
            kwargs["vertex_project"] = settings.vertex_project
            kwargs["vertex_location"] = settings.vertex_location
            quota_headers = _vertex_quota_headers()
            if quota_headers:
                kwargs["extra_headers"] = quota_headers
        for chunk in litellm.completion(**kwargs):
            try:
                delta = chunk.choices[0].delta.content
            except (AttributeError, IndexError, KeyError):
                delta = None
            if delta:
                yield delta
    except Exception:  # noqa: BLE001 - streaming is best-effort; caller falls back
        return


def call_with_retry(llm: LLM, prompt: str | list[dict], max_retries: int = 6) -> str:
    """Call an LLM, retrying on free-tier 429 rate-limit errors.

    `prompt` is passed straight through to `LLM.call()`, which accepts either
    a bare string (wrapped into a single user-role message) or an already-
    built `[{"role": ..., "content": ...}, ...]` list — callers should prefer
    the latter with a real `system` entry so persona/rules get a structural
    boundary from untrusted input instead of sharing the user role with it.

    Crew calls are throttled by the crew's `max_rpm`, but the standalone
    baseline and judge calls are not, so they wrap their calls here. On a 429 we
    honour the provider's suggested retry delay when present, else back off
    exponentially.
    """
    for attempt in range(max_retries):
        try:
            return llm.call(prompt)
        except Exception as exc:  # noqa: BLE001 - we re-raise if it's not a 429
            msg = str(exc)
            if "429" not in msg and "RESOURCE_EXHAUSTED" not in msg:
                # Not a rate-limit — map to a safe code rather than leaking the
                # provider exception upward as a raw string.
                raise AppError(ErrorCode.E_LLM_UNAVAILABLE, log_detail=msg) from exc
            if attempt == max_retries - 1:
                raise AppError(ErrorCode.E_LLM_RATE_LIMITED, log_detail=msg) from exc
            m = re.search(r"retry(?:Delay)?['\":\s]*([0-9]+(?:\.[0-9]+)?)s", msg)
            delay = float(m.group(1)) + 1 if m else min(2 ** attempt * 5, 60)
            time.sleep(delay)
    raise RuntimeError("unreachable")


_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001faff"  # symbols, pictographs, emoticons, transport, supplemental
    "\U00002600-\U000027bf"  # misc symbols & dingbats (☀-➿, includes ✨ ✈️)
    "\U0001f1e6-\U0001f1ff"  # regional indicator flags
    "\U0000fe00-\U0000fe0f"  # variation selectors
    "\U00002b00-\U00002bff"  # misc symbols and arrows
    "]+",
    flags=re.UNICODE,
)


def strip_emoji(text: str) -> str:
    """Deterministic backstop for the "no emoji" prompt instructions.

    A cheap model won't always honour a soft instruction reliably; this
    guarantees it regardless of what the LLM actually produced. Collapses any
    doubled-up whitespace the removal leaves behind.
    """
    return re.sub(r"[ \t]{2,}", " ", _EMOJI_RE.sub("", text))


# ---------------------------------------------------------------------------
# Prompt-injection defense
# ---------------------------------------------------------------------------
# Every prompt in this system interpolates untrusted text (user chat messages,
# freeform trip-request fields, web-search results) into the same string as
# the model's instructions — there is no structural instruction/data boundary
# the way a hardened system would enforce it. Two layers of defense:
#   1. Callers wrap untrusted text in explicit delimiter tags (see
#      conversation.py/baseline.py/tasks.py/tools.py) with a standing rule
#      that content inside those tags is never an instruction.
#   2. This regex backstop — the same "don't trust the model to reliably
#      follow a soft rule" pattern already used by strip_emoji above — catches
#      the common classic-injection phrasings and neutralises them *before*
#      they ever reach a prompt, so even if the delimiter convention is
#      ignored somewhere, the literal override text can't be read by the model.
# This is a pragmatic backstop, not a guarantee — it only catches known
# phrasings, not every possible injection wording.
_INJECTION_RE = re.compile(
    r"(ignore|disregard|forget)\s+(all|any|every|the)?\s*(previous|prior|above|earlier)"
    r"\s+(instructions?|prompts?|rules?|context)"
    r"|new\s+(system\s+)?(instructions?|prompt)\s*[:\-]"
    r"|you\s+are\s+now\s+(in\s+)?(a\s+)?(developer|admin|god|jailbreak|dan)\s*mode"
    r"|reveal\s+(your|the)\s+(system\s+)?(prompt|instructions)"
    r"|(act|pretend to be|pretend you are)\s+as\s+(if\s+you\s+(have|had)\s+no|an?\s+(ai\s+)?(with\s+no|unfiltered))"
    r"\s+(restrictions?|rules?|guidelines?|filters?)"
    r"|do\s+anything\s+now"
    r"|override\s+(your|the)\s+(system\s+)?(instructions?|rules?)"
    r"|(this\s+is\s+)?(a\s+)?system\s*[:\-]\s*(you\s+must|new)"
    r"|<\s*/?\s*(system|instructions?)\s*>",
    re.IGNORECASE,
)


def sanitize_untrusted(text: str | None) -> str:
    """Redact classic prompt-injection phrasings from untrusted free text.

    Applied to text that gets re-embedded into other LLM prompts downstream
    (trip-request freeform fields, tool outputs) so an override attempt that
    slips past the extraction step can't propagate further. Not applied to
    the raw chat transcript itself (see the delimiter convention in
    conversation.py) since that would degrade legitimate conversation.
    """
    if not text:
        return text or ""
    return _INJECTION_RE.sub("[content removed: resembled an instruction-override attempt]", text)


def extract_json(text: str) -> dict:
    """Best-effort parse of a JSON object from an LLM response.

    Strips markdown code fences and any leading/trailing prose, then parses
    the outermost {...} span. Shared by the baseline and the chat/extraction
    layer, both of which prompt the model for a bare JSON object back.
    """
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)
