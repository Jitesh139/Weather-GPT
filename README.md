# WeatherGPT

A conversational (voice + text) weather Q&A system built for SIH. The
core rule the whole system is designed around: **no numeric weather
value is ever shown to the user unless it came from a real Open-Meteo
API call made during that request.** See `weathergpt-build-spec (1).md`
for the full engineering spec this implementation follows.

## Architecture

```
User (voice or text)
      -> STT: Web Speech API (browser) OR Bhashini ASR OR Google/Gemini ASR (via backend proxy)
      -> Router (rule-based, FAST vs SLOW)
           FAST -> extract location/param -> Open-Meteo + Google Weather (blended) -> validate -> template
           SLOW -> Generator LLM (tool-calls Open-Meteo + Google Weather) -> Verifier LLM/programmatic diff
                     -> on failure: retry once, else fall back to FAST template
      -> TTS: Web Speech API (browser) OR Bhashini TTS OR Gemini TTS (via backend proxy)
```

Voice provider is switchable via `VOICE_PROVIDER` (see below) - the
frontend queries `GET /voice/config` once at load to decide which flow
to use. Bhashini/Gemini credentials never reach the browser: `/voice/asr`
and `/voice/tts` proxy through the backend, which is the only place that
holds `BHASHINI_USER_ID`/`BHASHINI_API_KEY` or `GEMINI_API_KEY`.

Every weather answer is grounded in Open-Meteo (the original, required
source); when `GOOGLE_WEATHER_API_KEY` is also set, the Google Weather API
is queried as a second, independent source and blended into a "best
estimate" alongside both raw readings (see "Combined weather sources"
below) - this is additive only, never a hard dependency.

Every request logs its routing decision, per-stage latency, cache hit/
miss, and verification outcome to Postgres (`query_log`,
`verification_log`, `cached_forecast`, `user_preference`) - not just
console output - so this data survives a restart.

## Running it

1. Copy the env template and fill in your keys:
   ```
   cp .env.example .env
   ```
   Fill in `ANTHROPIC_API_KEY` and `GEMINI_API_KEY`. The app boots and
   the FAST path works with these left blank - only SLOW-path (reasoning)
   queries need them, and will fail with an honest error until they're set.

2. Start everything with one command:
   ```
   docker compose up --build
   ```
   This starts Postgres, waits for it to be healthy, runs Alembic
   migrations, then starts the backend.

3. Open **http://localhost:8000/** for the web UI, or
   **http://localhost:8000/docs** for the interactive API docs.

### Environment variables to fill in (`.env`)

| Variable | Required for | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | SLOW path (default generator) | https://console.anthropic.com/ |
| `GEMINI_API_KEY` | SLOW path (default verifier) | https://aistudio.google.com/apikey |
| `DATABASE_URL` | Everything | Pre-filled to match the compose Postgres service - only change if running Postgres elsewhere |
| `CACHE_TTL_SECONDS` | Optional | Defaults to 900 (15 min) |
| `LOG_LEVEL` | Optional | Defaults to `info` |
| `GENERATOR_PROVIDER` / `VERIFIER_PROVIDER` | Optional | Defaults `claude` / `gemini` - see "Open decisions" below |
| `GENERATOR_MODEL` / `VERIFIER_MODEL` | Optional | Defaults `claude-opus-5` / `gemini-3.6-flash` - both confirmed working against the live APIs during development |
| `GOOGLE_WEATHER_API_KEY` | Optional | When set, every weather answer also cross-checks Open-Meteo against the Google Weather API - see "Combined weather sources" below. Get one (with the Weather API enabled) at https://console.cloud.google.com/ |
| `VOICE_PROVIDER` | Optional | `web_speech` (default, zero-config), `bhashini`, or `google` |
| `BHASHINI_USER_ID` / `BHASHINI_API_KEY` | Voice, only if `VOICE_PROVIDER=bhashini` | Register at https://bhashini.gov.in/ulca/user/register, generate a key from "My Profile" |
| `BHASHINI_PIPELINE_ID` / `BHASHINI_LANGUAGE` / `BHASHINI_TTS_GENDER` | Optional (Bhashini only) | Defaults to a community-referenced pipeline ID, Hindi, female voice - see caveats below |
| `GOOGLE_TRANSLATE_MODEL` / `GOOGLE_TTS_MODEL` | Optional (`VOICE_PROVIDER=google` only) | Only needs `GEMINI_API_KEY` above - no separate signup. See "Google voice integration" below |

## Running tests locally (no Docker, no API keys needed)

```
cd backend
pip install -r requirements.txt
pytest tests/ -v
```

All 66 tests (65 run, 1 real-network test skipped by default) pass with
zero API keys configured - every LLM and Bhashini call is mocked, and
Open-Meteo calls are mocked via `respx` except one explicitly-marked
real-network test.

## API contract

`POST /query`

Request: `{"text": "...", "input_mode": "voice"|"text"}`

Success: `{"answer": "...", "path": "fast"|"slow", "verified": true, "source_data": {...}, "latency_ms": 1234, "error": null}`

Failure: `{"answer": null, "error": "...", "path": ..., "verified": false, "latency_ms": ...}`

The frontend (`frontend/app.js`) handles the failure shape explicitly -
it always shows/speaks an honest message, never a silent blank state.

## Verified against the real APIs

Both provider-split directions were run end-to-end against the live
Anthropic and Gemini APIs (not just mocked tests) during development:

- **Claude generates / Gemini verifies** (the default): confirmed
  working with `claude-opus-5` + `gemini-3.6-flash`.
- **Gemini generates / Claude verifies**: confirmed working with
  `gemini-3.5-flash-lite` + `claude-opus-5`.

This surfaced two real bugs that are now fixed and covered by
regression tests (`tests/test_llm_client_gemini.py`,
`tests/test_integration.py::test_slow_path_unexpected_exception_returns_honest_error_not_crash`):

1. The `anthropic` SDK had to be upgraded from an initial 0.69.0 pin to
   1.4.0 - the structured-output feature used for the verifier
   (`output_config`) didn't exist in 0.69.0.
2. `google-genai` had to be upgraded from 1.3.0 to 2.22.0 - the older
   version couldn't correctly round-trip `thought_signature` data on
   function-call turns for newer "thinking" Gemini models, causing a
   hard 400 from the API.
3. The verifier's JSON schema included `"additionalProperties": false`
   (required for Anthropic's structured output) - Gemini's
   `response_schema` rejects that key outright with a 400. Fixed by
   giving `GeminiClient.verify()` its own schema variant without it.
4. `main.py`'s slow-path exception handling only caught this project's
   own custom exception types, so an unexpected provider-SDK error
   (like #3, before it was fixed) surfaced as a raw HTTP 500 with no
   body - worse than the spec's required honest-failure behavior. Added
   a catch-all on both the fast and slow paths so *any* unexpected
   failure now returns the proper `{error, answer: null}` shape instead
   of crashing.
5. The original `gemini-2.5-flash` default verifier model was
   deprecated by Google (returns "no longer available to new users" as
   of testing) - the live API error itself named the successor,
   `gemini-3.6-flash`, which is now the default.
6. The default `GOOGLE_TTS_MODEL` (`gemini-3.1-flash-tts`) turned out not
   to exist - the live API returned a 404 naming the real model,
   `gemini-3.1-flash-tts-preview` (confirmed via `client.models.list()`
   against the real API key), which is now the default.
7. The Gemini Live translate model (`gemini-3.5-live-translate-preview`,
   used for ASR - see "Google voice integration" below) does not
   reliably send `turn_complete`, and sending a whole pre-recorded clip
   in one instantaneous burst caused the server to only partially
   transcribe it. Fixed by pacing the audio send to real-time (matching
   each 100ms chunk's actual duration) with 1s of trailing silence for
   server-side voice-activity detection, and capping the receive loop by
   message count instead of waiting for `turn_complete`.

## Combined weather sources

When `GOOGLE_WEATHER_API_KEY` is set, `services/weather.fetch_combined_forecast`
calls Open-Meteo (required, always the base answer) and the Google
Weather API (`weather.googleapis.com/v1/currentConditions:lookup`,
optional) for the same coordinates, then blends the two into a
`best_estimate` (both are real, grounded numbers from calls made this
request, so averaging them is not an ungrounded guess). Both raw readings
plus the blended value are included in the answer and in `source_data` -
nothing is hidden. This is purely additive: if the key is unset or the
Google call fails for any reason, the answer is exactly what it would
have been with Open-Meteo alone (confirmed by
`test_fetch_combined_forecast_falls_back_when_google_fails`). Verified
live end-to-end (e.g. Chennai: Open-Meteo 27.3°C, Google 30.8°C, best
estimate 29.1°C, `verified: true`).

Precipitation is deliberately NOT blended into a single number - Open-
Meteo's `precipitation` is an instantaneous/last-hour reading while
Google's `qpf` is a forecast quantity over a different window, so both
are surfaced raw instead of averaging measurements that aren't the same
thing.

## Google voice integration - verified live

Bhashini registration wasn't available, so `VOICE_PROVIDER=google` routes
voice through Gemini instead - both directions authenticate with the
same `GEMINI_API_KEY` already used by the LLM pipeline, no separate
signup needed. This was run live end-to-end during development (unlike
the Bhashini integration below, which remains unverified):

- **ASR** (`services/google_voice_client.speech_to_text`): uses
  `gemini-3.5-live-translate-preview` (Gemini's Live API), configured to
  translate into English regardless of the spoken language, so the
  keyword-based router always gets a usable transcript. Confirmed live
  with English input ("The temperature in Chennai is 29 degrees." ->
  transcribed correctly) and with real Hindi speech synthesized via
  Gemini TTS ("आज मुंबई में मौसम कैसा है?" -> correctly translated to
  "What's the weather like in Mumbai today?", which then round-tripped
  through `/query` to a correct, verified, combined-source answer).
- **TTS** (`services/google_voice_client.text_to_speech`): uses Gemini's
  native (non-live) TTS model, `gemini-3.1-flash-tts-preview`. Always
  speaks the reply in English, regardless of the input language - the
  live translate model above is audio-in/audio-out only and can't
  synthesize speech from arbitrary already-generated answer text, and
  this build doesn't add a second text-translate step to work around
  that. A real limitation, not silently hidden.
- **Latency**: a single ASR round-trip took ~17-20 seconds live (the
  audio has to be sent at real-time pace, not just uploaded instantly -
  see bug #7 above), and TTS took a few seconds. Both are "preview"-tier
  Google models; expect this to improve as they mature, but budget for it
  in a live demo.

## Bhashini voice integration - built, but NOT verified live

The original build spec (Section 13) said to leave Bhashini as a
swappable interface only, not implement it. Per an explicit later
request, it's now a real implementation - kept alongside the Google
voice provider above as a second `VOICE_PROVIDER` option, in case
Bhashini access becomes available later -
`backend/services/bhashini_client.py`
plus `/voice/config`, `/voice/asr`, `/voice/tts` endpoints and a
`BhashiniProvider` in `frontend/app.js` that records mic audio, encodes
it as WAV client-side (Web Audio API - no library dependency), and
proxies through the backend for both ASR and TTS.

**What this is grounded in:** the exact request/response JSON shapes,
endpoint URLs, and header names were pulled directly from Bhashini's own
API documentation (bhashini.gitbook.io/bhashini-apis) via live fetches
during this build - they are not guessed. All 15 Bhashini-related tests
(`tests/test_bhashini.py`, `tests/test_voice_routes.py`) pass against
mocked responses shaped exactly like that documentation.

**What is NOT verified:** no Bhashini account/credentials were available
during this build, so the integration has never made a real call to
Bhashini's servers. Unlike the Anthropic/Gemini integration (which was
run live and had 3 real bugs found and fixed), this code is
correct-per-the-docs but unexercised. Specific known risks to check
first when you add real credentials:

1. **`BHASHINI_PIPELINE_ID` default** (`64392f96daac500b55c543cd`) is a
   value referenced in community integrations, not something confirmed
   against your account's available pipelines - verify via the Bhashini
   portal or its Pipeline Search API.
2. **`BHASHINI_LANGUAGE` default is `hi`** (Hindi) - Bhashini's model
   catalog is Indian-language-focused, and English ASR/TTS support is
   not guaranteed to exist. If you need English, check the pipeline
   config call's response for supported languages before relying on it.
3. **Audio format**: the browser records via `ScriptProcessorNode`
   (deprecated but universally supported, chosen to avoid adding an
   AudioWorklet module file) and hand-encodes a 16-bit PCM WAV file at
   whatever sample rate the browser's `AudioContext` actually uses
   (typically 44100 or 48000 Hz) - sent to Bhashini as the declared
   `samplingRate`. This should work per Bhashini's documented minimum of
   8000 Hz, but hasn't been confirmed against a real ASR response.
4. Once you have credentials, test with `VOICE_PROVIDER=bhashini` in
   `.env`, restart, click the mic, and check the browser console /
   backend logs if the transcript comes back empty or an error appears.

## Judgment calls made where the spec left something open

These were flagged rather than silently decided - see the plan file for
full reasoning on each:

1. **Provider split (spec Section 14.1, not finalized):** default is
   Claude generates, Gemini verifies - two different vendors for genuine
   verifier independence. Swap via `GENERATOR_PROVIDER`/`VERIFIER_PROVIDER`
   in `.env`, no code changes needed.
2. **Latency threshold (spec Section 14.2):** not set. Per-stage latency
   is now logged to `query_log` for every request, so a real threshold
   can be chosen from actual data once the system has run for a while.
3. **Router confidence threshold (spec Section 14.3):** the router is
   pure keyword matching, not a scored classifier, so there's no
   numeric threshold to set yet - relevant only if it's later upgraded
   to a scored/ML classifier.
4. **Frontend serving:** served via FastAPI's `StaticFiles`, no separate
   frontend container, since the spec's compose description names only
   backend + Postgres.
5. **TTS "streaming":** implemented as sentence-chunked speech after the
   single `/query` JSON response returns, since spec Section 9's
   single-response contract is authoritative and true token-level
   streaming would require changing it (e.g. to SSE).
6. **Geocoding cache** reuses the `cached_forecast` table (long TTL,
   sentinel `parameter` value) instead of adding a 5th table, staying
   within the spec's four "minimum tables."
7. **`user_preference` keying** uses an anonymous browser-localStorage
   client ID, since no auth/session system exists anywhere else in the
   spec. The table is not yet read/written by the query pipeline -
   explicitly forward-looking per the spec.
8. **Slow-path double-verification-failure fallback** re-extracts the
   location from the original query and serves the fast-path template;
   if a query needs multiple locations (e.g. "compare Mumbai and
   Delhi") and only one is extractable, an honest error is returned
   rather than a partial/misleading single-city answer.
9. **DB column types** use SQLAlchemy's generic `Uuid`/`JSON` types
   rather than `postgresql.UUID`/`JSONB`, so the exact same models run
   against both the real Postgres (via docker-compose) and an
   in-memory-friendly SQLite file for the test suite - avoids requiring
   a live Postgres just to run `pytest`.

## What was explicitly not built (per spec Section 13)

IMD/NDMA/Sachet alerts, raw NOAA GFS/WRF ingestion, MOSDAC satellite
data, crop-calendar agronomic advice (the generator is prompted to add
an explicit "general guidance, not verified" caveat instead), and
WhatsApp/IVR/SMS delivery. (Bhashini was originally in this list too,
per spec Section 13 - it's since been implemented for real; see the
dedicated section above for what that means and doesn't mean.)
