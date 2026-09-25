"""Shared pytest fixtures. Not in the spec's literal Section 8 file list,
but required for pytest to share DB/client setup across the test files
that section does list - see plan judgment-call notes.

Uses a throwaway SQLite file DB rather than requiring a live Postgres,
so `pytest` runs standalone with no docker-compose stack up. All LLM
calls are mocked via FakeLLMClient - the suite requires zero real API
keys (spec Section 12's last bullet).
"""
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

TEST_DB_PATH = BACKEND_DIR / "tests" / "_test_weathergpt.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("GEMINI_API_KEY", "")
os.environ.setdefault("GOOGLE_WEATHER_API_KEY", "")
# Pinned, not merely defaulted-in-code: config.py reads the project-root
# .env, so without these the suite would assert against whichever
# provider a developer happens to have configured locally rather than
# against the documented defaults.
os.environ["VOICE_PROVIDER"] = "web_speech"
os.environ["GENERATOR_PROVIDER"] = "claude"
os.environ["VERIFIER_PROVIDER"] = "gemini"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from db.models import Base, CachedForecast  # noqa: E402
from db.session import SessionLocal, engine  # noqa: E402
from models.schemas import VerificationResult  # noqa: E402
from services.llm_client import GenerationResult, LLMClient  # noqa: E402
from config import settings as _settings  # noqa: E402

# Can't be pinned via os.environ above (an empty value fails validation),
# and a local .env setting it would send tests to a real TTS provider.
_settings.voice_tts_provider = None

SAMPLE_GEOCODE_RESPONSE = {
    "results": [{"name": "Mumbai", "latitude": 19.076, "longitude": 72.8777, "country": "India"}]
}

SAMPLE_FORECAST_RESPONSE = {
    "current": {
        "temperature_2m": 29.5,
        "relative_humidity_2m": 70,
        "precipitation": 0.0,
        "weather_code": 1,
        "wind_speed_10m": 3.2,
    },
    "hourly": {
        "temperature_2m": [29.5, 30.1],
        "precipitation_probability": [10, 15],
        "precipitation": [0.0, 0.0],
        "weather_code": [1, 1],
    },
    "daily": {
        "temperature_2m_max": [33.0],
        "temperature_2m_min": [26.0],
        "precipitation_sum": [0.0],
        "precipitation_probability_max": [20],
        "weather_code": [1],
    },
}


@pytest.fixture(scope="session", autouse=True)
def _setup_db():
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    Base.metadata.create_all(engine)
    yield
    engine.dispose()
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()


@pytest.fixture(autouse=True)
def _fresh_sdk_clients():
    """SDK clients are cached per process; tests that fake the SDK
    constructor need each test to build its own."""
    from services.llm_client import anthropic_sdk_client, genai_sdk_client

    anthropic_sdk_client.cache_clear()
    genai_sdk_client.cache_clear()
    yield
    anthropic_sdk_client.cache_clear()
    genai_sdk_client.cache_clear()


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _isolated_cache(db_session):
    """The weather cache (cache/store.py) is a process-wide singleton
    backed by a shared table, so without resetting it between tests, a
    cache hit in one test can mask a different mocked response in
    another test that happens to reuse the same location/coordinates.
    """
    from cache.store import cache

    cache._mem.clear()
    db_session.query(CachedForecast).delete()
    db_session.commit()
    yield
    cache._mem.clear()


@pytest.fixture
def client(db_session):
    import main as main_module

    def _override():
        yield db_session

    main_module.app.dependency_overrides[main_module.get_db] = _override
    with TestClient(main_module.app) as c:
        yield c
    main_module.app.dependency_overrides.clear()


class FakeLLMClient(LLMClient):
    """Configurable stand-in for AnthropicClient/GeminiClient. Lets tests
    exercise generator.py/verifier.py/main.py without any real API key
    or network call.
    """

    def __init__(
        self,
        generate_fn: Optional[Callable[..., GenerationResult]] = None,
        verify_fn: Optional[Callable[..., VerificationResult]] = None,
    ):
        self._generate_fn = generate_fn
        self._verify_fn = verify_fn
        self.generate_calls: list[dict[str, Any]] = []
        self.verify_calls: list[dict[str, Any]] = []

    def generate_with_tools(self, system_prompt, user_query, tools, tool_executor, max_tool_iterations=3):
        self.generate_calls.append({"system_prompt": system_prompt, "user_query": user_query})
        if self._generate_fn is None:
            raise NotImplementedError("FakeLLMClient.generate_with_tools not configured for this test")
        return self._generate_fn(system_prompt, user_query, tools, tool_executor, max_tool_iterations)

    def verify(self, draft_answer, source_data, claims_to_check=None):
        self.verify_calls.append({"draft_answer": draft_answer, "source_data": source_data})
        if self._verify_fn is None:
            raise NotImplementedError("FakeLLMClient.verify not configured for this test")
        return self._verify_fn(draft_answer, source_data, claims_to_check)
