"""Bhashini ASR/TTS integration (spec Section 7's swappable voice-layer
interface, now implemented for real per explicit user request - this
goes beyond the original build spec's Section 13, which said to leave
Bhashini as an interface only. Flagged clearly: this has NOT been
exercised against a real Bhashini account in this build (no credentials
were available) - the request/response shapes below come directly from
Bhashini's own API documentation (bhashini.gitbook.io/bhashini-apis),
not from a live test. Verify end-to-end once BHASHINI_USER_ID and
BHASHINI_API_KEY are set.

Bhashini's inference flow is two calls:
  1. Pipeline Config Call - fixed endpoint, authenticated with
     userID/ulcaApiKey headers. Given a source language + task list, it
     returns which serviceId to use for each task, plus a
     pipelineInferenceAPIEndPoint (a callback URL + a dynamic auth
     header) to use for the second call.
  2. Pipeline Compute Call - POST to the callback URL from step 1, using
     the dynamic auth header (NOT userID/ulcaApiKey) - this is what
     actually runs ASR or TTS and returns the result.
"""
import logging
from typing import Any, Literal

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import settings

logger = logging.getLogger(__name__)

CONFIG_URL = "https://meity-auth.ulcacontrib.org/ulca/apis/v0/model/getModelsPipeline"

TaskType = Literal["asr", "tts"]


class BhashiniConfigError(Exception):
    """Raised when BHASHINI_USER_ID/BHASHINI_API_KEY are missing."""


class BhashiniError(Exception):
    """Non-retryable failure talking to Bhashini (bad response shape,
    unsupported language/task, etc.)."""


class TransientBhashiniError(Exception):
    """Retryable failure - network hiccup, upstream 5xx, timeout."""


def _require_credentials() -> None:
    if not (settings.bhashini_user_id and settings.bhashini_api_key):
        raise BhashiniConfigError("BHASHINI_USER_ID/BHASHINI_API_KEY are not set")


def _raise_for_transient(exc: httpx.HTTPStatusError) -> None:
    if exc.response.status_code >= 500 or exc.response.status_code == 429:
        raise TransientBhashiniError(str(exc)) from exc
    raise BhashiniError(f"Bhashini request failed ({exc.response.status_code}): {exc.response.text}") from exc


@retry(
    retry=retry_if_exception_type(TransientBhashiniError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def _post_json(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    try:
        response = httpx.post(url, headers=headers, json=body, timeout=30.0)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        _raise_for_transient(exc)
        raise  # unreachable
    except (httpx.TransportError, httpx.TimeoutException) as exc:
        raise TransientBhashiniError(str(exc)) from exc


# Pipeline config responses are stable for a given (language, tasks)
# combination - cached in-process only (not Postgres-backed like the
# weather cache; this is a much lighter-weight, short-lived lookup, not
# user-facing forecast data covered by spec's persistence requirement).
_pipeline_config_cache: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}


def _get_pipeline_config(source_language: str, tasks: list[TaskType]) -> dict[str, Any]:
    _require_credentials()
    cache_key = (source_language, tuple(sorted(tasks)))
    if cache_key in _pipeline_config_cache:
        return _pipeline_config_cache[cache_key]

    body = {
        "pipelineTasks": [
            {"taskType": task, "config": {"language": {"sourceLanguage": source_language}}} for task in tasks
        ],
        "pipelineRequestConfig": {"pipelineId": settings.bhashini_pipeline_id},
    }
    headers = {
        "userID": settings.bhashini_user_id,
        "ulcaApiKey": settings.bhashini_api_key,
        "Content-Type": "application/json",
    }

    data = _post_json(CONFIG_URL, headers, body)

    endpoint = data.get("pipelineInferenceAPIEndPoint")
    if not endpoint or "callbackUrl" not in endpoint or "inferenceApiKey" not in endpoint:
        raise BhashiniError("Bhashini pipeline config response missing pipelineInferenceAPIEndPoint")

    service_ids: dict[str, str] = {}
    for task_config in data.get("pipelineResponseConfig", []):
        task_type = task_config.get("taskType")
        configs = task_config.get("config") or []
        if not configs:
            raise BhashiniError(f"Bhashini did not return a service for task '{task_type}' + language '{source_language}'")
        service_ids[task_type] = configs[0]["serviceId"]

    resolved = {
        "callback_url": endpoint["callbackUrl"],
        "auth_header_name": endpoint["inferenceApiKey"]["name"],
        "auth_header_value": endpoint["inferenceApiKey"]["value"],
        "service_ids": service_ids,
    }
    _pipeline_config_cache[cache_key] = resolved
    return resolved


def speech_to_text(audio_base64: str, source_language: str, audio_format: str = "wav", sampling_rate: int = 16000) -> str:
    """Returns the transcript for the given base64-encoded audio."""
    pipeline = _get_pipeline_config(source_language, ["asr"])

    body = {
        "pipelineTasks": [
            {
                "taskType": "asr",
                "config": {
                    "language": {"sourceLanguage": source_language},
                    "serviceId": pipeline["service_ids"]["asr"],
                    "audioFormat": audio_format,
                    "samplingRate": sampling_rate,
                },
            }
        ],
        "inputData": {"input": [{"source": None}], "audio": [{"audioContent": audio_base64}]},
    }
    headers = {
        pipeline["auth_header_name"]: pipeline["auth_header_value"],
        "Content-Type": "application/json",
    }

    data = _post_json(pipeline["callback_url"], headers, body)

    try:
        return data["pipelineResponse"][0]["output"][0]["source"]
    except (KeyError, IndexError, TypeError) as exc:
        raise BhashiniError(f"Unexpected Bhashini ASR response shape: {data}") from exc


def text_to_speech(text: str, source_language: str, gender: str = "female") -> str:
    """Returns base64-encoded WAV audio for the given text."""
    pipeline = _get_pipeline_config(source_language, ["tts"])

    body = {
        "pipelineTasks": [
            {
                "taskType": "tts",
                "config": {
                    "language": {"sourceLanguage": source_language},
                    "serviceId": pipeline["service_ids"]["tts"],
                    "gender": gender,
                },
            }
        ],
        "inputData": {"input": [{"source": text}], "audio": [{"audioContent": None}]},
    }
    headers = {
        pipeline["auth_header_name"]: pipeline["auth_header_value"],
        "Content-Type": "application/json",
    }

    data = _post_json(pipeline["callback_url"], headers, body)

    try:
        return data["pipelineResponse"][0]["audio"][0]["audioContent"]
    except (KeyError, IndexError, TypeError) as exc:
        raise BhashiniError(f"Unexpected Bhashini TTS response shape: {data}") from exc
