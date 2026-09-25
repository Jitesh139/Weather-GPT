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

Voice provider is switchable via `VOICE_PROVIDER` (see below).
Bhashini/Gemini credentials never reach the browser: `/voice/asr` and
`/voice/tts` proxy through the backend, which is the only place that holds
`BHASHINI_USER_ID`/`BHASHINI_API_KEY` or `GEMINI_API_KEY`.

The React frontend calls `GET /voice/config` once at load and picks the
matching client flow (`frontend/src/lib/voice.ts`):

- `web_speech` - the browser's own SpeechRecognition/speechSynthesis
  handle both directions. Zero config, zero quota, Chrome/Edge only.
- `bhashini` / `google` - the browser records mic audio, encodes it as
  16-bit mono WAV (neither ASR provider accepts the webm/opus a
  `MediaRecorder` produces) and posts it to `/voice/asr`; the answer is
  spoken by posting it to `/voice/tts` and playing the returned WAV. One
  TTS request per answer, not per sentence, since provider quotas are
  counted per request.

Either way the mic button, recording animation and error card are the
same UI - only the transport changes. If `/voice/config` is unreachable,
voice falls back to the browser-native flow rather than going dead.

Every weather answer is grounded in Open-Meteo (the original, required
source); when `GOOGLE_WEATHER_API_KEY` is also set, the Google Weather API
is queried as a second, independent source and blended into a "best
estimate" alongside both raw readings (see "Combined weather sources"
below) - this is additive only, never a hard dependency.

Every request logs its routing decision, per-stage latency, cache hit/
miss, and verification outcome to Postgres (`query_log`,
`verification_log`, `cached_forecast`, `user_preference`) - not just
console output - so this data survives a restart.

## Layout

```
backend/          FastAPI app - router, weather services, LLM pipeline, DB
frontend/         Vite + React + Tailwind UI (the one you see)
legacy-frontend/  The original vanilla HTML/JS UI, kept for reference only
```

The frontend talks to the backend over plain same-origin `fetch` calls to
`POST /query` (`frontend/src/lib/api.ts`). Nothing else in the UI knows
the API exists. There are two ways to run that are both same-origin, so
no CORS setup and no API base URL to configure:

- **Dev**: the Vite dev server proxies `/query`, `/health` and `/voice/*`
  to `http://127.0.0.1:8000` (`frontend/vite.config.ts`).
- **Production**: the backend serves the built frontend itself - it mounts
  `frontend/dist` at `/` (see the bottom of `backend/main.py`).

Set `VITE_API_BASE` at build time only if you deploy the API on a
different origin.

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
   This builds the frontend into `frontend/dist`, starts Postgres, waits
   for it to be healthy, runs Alembic migrations, then starts the backend
   with that build mounted at `/`.

3. Open **http://localhost:8000/** for the web UI, or
   **http://localhost:8000/docs** for the interactive API docs.

### Running it without Docker (local dev, hot reload)

```
.\dev.ps1
```

Opens the backend on **http://localhost:8000** and the Vite dev server on
**http://localhost:5173** - open the second one. No Docker and no Postgres
needed: `dev.ps1` defaults `DATABASE_URL` to the SQLite file
`backend/dev.db` (`.env`'s value points at the Compose Postgres service,
which only resolves inside the compose network). Export `DATABASE_URL`
yourself first to use a real Postgres.

The equivalent by hand:

```
cd backend
$env:DATABASE_URL = "sqlite:///dev.db"
..\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000

cd frontend          # in a second terminal
npm install
npm run dev
```

To serve the real build off the backend alone (single server, no Vite),
run `npm run build` in `frontend/` and open http://localhost:8000/ - the
backend picks up `frontend/dist` automatically.

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
| `GOOGLE_ASR_MODEL` / `GOOGLE_TTS_MODEL` | Optional (`VOICE_PROVIDER=google` only) | Only needs `GEMINI_API_KEY` above - no separate signup. See "Google voice integration" below |

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

The frontend handles the failure shape explicitly - `fetchWeatherQuery`
in `frontend/src/lib/api.ts` rejects on `{answer: null, error}` (and on an
unreachable backend), so the UI always shows an honest error message,
never a silent blank state.

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
7. ASR originally used the Gemini Live translate model, which had to be
   fed audio at real-time pace and took ~20s for a 3s clip (and sometimes
   dropped words). Replaced by one-shot transcription with
   `gemini-3.5-flash-lite` (~1.7s).

## Researcher dashboard

Selecting **Researcher** on the persona screen opens `/dashboard/researcher`
- a distinct route, not another section of the chat page. The
conversational interface stays one click away ("Ask a question"), and the
dashboard reuses the same colour tokens, typography and panel treatment as
the rest of the product.

| Feature | Endpoint | LLM involved |
|---|---|---|
| Multi-model forecast comparison | `GET /research/compare-models` | No |
| Historical climate trend | `GET /research/historical-trend` | No |
| Regional context (Arabian Sea / Bay of Bengal) | `GET /research/regional-context` | No |
| Optional regional description | `GET /research/regional-context/narrative` | Yes - generate + verify |

**Multi-model comparison** requests several underlying models from
Open-Meteo in one call (`models=` - the response suffixes each variable
with the model id) and plots one line per model on a shared axis, plus the
widest single-timestep disagreement. Every model id in the catalog was
probed against the live API; the docs list some names the API rejects
(`ecmwf_ifs`, `meteo_france_seamless`), so re-probe before adding more.

**Historical trend** queries the Open-Meteo archive (`archive-api`, data
back to 1940) and aggregates daily observations to monthly or yearly
buckets server-side, with a rolling average and mean/min/max. Temperature
averages and precipitation sums - averaging daily rainfall totals would
understate a month roughly thirtyfold. No LLM touches this: it is
arithmetic, and arithmetic does not belong in a language model.

**Regional context** samples six open-ocean points across the two seas and
six coastal cities, concurrently, and shows them side by side with
sea-level pressure and gusts. It asserts no causal link. The optional
description is the only LLM call on the dashboard and goes through the
*same* pipeline as a conversational answer - tool-enforced generation,
programmatic numeric diff, LLM qualitative check, one retry on failure,
and no unverified draft is ever displayed. Its prompt explicitly forbids
causal or predictive claims ("will bring", "is heading towards"); general
meteorological context is allowed once and must be framed as such.

**Not built:** GNN-based cloud/storm tracking from satellite imagery. The
dashboard carries a labelled "future research direction" card for it and
simulates nothing - no placeholder tracks, no synthetic output.

## Performance

Response latency was dominated by the SLOW path's two LLM stages. Measured
from the `query_log` table, before and after:

| Stage | Before | After |
|---|---|---|
| Verification | 3.2-23.8 s | 2.2-10.5 s |
| Generation | 4.0-15.6 s | 6.4-7.1 s |
| Simple Hindi lookup, end to end | 16.6 s | **0 ms** (cached) / ~1 s cold |

Three changes did it:

1. **Verifier effort.** The Stage 2 verifier runs Claude Opus 5, where
   adaptive thinking is on by default. By the time it runs, every number
   has already been checked by the programmatic diff - all that is left is
   judging a couple of qualitative phrases. Running it at `effort: "low"`
   cut it by roughly 2-3x with nothing to scrutinise more deeply.
2. **Concurrent providers.** Open-Meteo and the Google Weather cross-check
   are independent once the location resolves, so on a cache miss they now
   go out together instead of one after the other. Only the HTTP calls run
   off-thread - the SQLAlchemy Session stays on the request thread.
3. **Hindi FAST routing.** The router's FAST patterns were English-only, so
   every Hindi question - including plain lookups - paid a full two-stage
   LLM round trip. Hindi/Hinglish patterns now route those to the template
   path, gated on a location actually being extractable so a question the
   SLOW path could answer never gets "please specify a city" instead.

## Multilingual answers

A question is answered in the language it was asked in. Two mechanisms,
because the two paths have different capabilities:

- **SLOW path** (LLM): the generator is instructed to reply in the same
  language *and script* the user wrote in. This needs no translation
  table and works for any language the model speaks - including Hindi
  typed or transcribed in Latin script ("aaj ka mausam kaisa hai"), which
  is how people actually type it.
- **FAST path** (templates): can only answer in languages it has
  templates for - currently English and Hindi (`services/fast_path.py`),
  with English as the fallback for anything else. `services/language.py`
  picks between them with a Devanagari script check plus a Hinglish
  keyword list.

Non-English questions match none of the router's English FAST patterns,
so in practice they take the SLOW path and get a real generated answer
rather than a template. The FAST templates matter mainly for the
verification-failed fallback, which must not silently switch the user
back to English.

Numbers and place names are never translated - only the sentence around
them - so the verifier's programmatic numeric diff still works unchanged
on a Hindi draft.

Verified live: "Aaj Ka Mausam kaisa hai Bhopal mein" -> answered in
Hinglish; "भोपाल में आज मौसम कैसा है" -> answered in Devanagari; English
questions unchanged. All three verified and grounded.

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

- **ASR** (`services/google_voice_client.speech_to_text`): a single
  `generate_content` call to `GOOGLE_ASR_MODEL` (default
  `gemini-3.5-flash-lite`, minimal thinking) with the audio inline. It
  returns what was said in the speaker's own language, so the answer can
  come back in it.
- **TTS** (`services/google_voice_client.text_to_speech`): uses Gemini's
  native (non-live) TTS model, `gemini-3.1-flash-tts-preview`. Speaks
  whatever language the answer text is in, which is the user's own.
- **Latency**: ASR ~1.7s. Gemini TTS measured ~12s for a typical answer,
  which is why `VOICE_TTS_PROVIDER` can route speech output to Bhashini
  while ASR stays on Gemini.

## Bhashini voice integration - verified live

Tested against a real account: TTS ~0.5-1s (Hindi and English), ASR ~0.6-2s
(Hindi). The recommended setup is `VOICE_PROVIDER=google` (Gemini ASR, which
handles Hinglish and English) with `VOICE_TTS_PROVIDER=bhashini`. The live test
found one bug, now fixed: ASR requests must not include a null `input` entry.

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
