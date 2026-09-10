# WeatherGPT — Engineering Build Specification

This document is a build spec, not a pitch deck. It describes what to build, how the pieces fit together, and the behavior expected at each boundary. Build this as production software: proper error handling, no silent failures, no hardcoded shortcuts that would need to be ripped out later.

---

## 1. Problem & Product Goal

Weather data (NWP model output, satellite products, bulletins) already exists but is scattered and technical. The product goal is a conversational system — voice and text — that lets a person ask a plain-language weather question and get a correct, spoken, trustworthy answer. The system must never present a number to the user that isn't traceable to a real data source. This constraint is not optional — it is the core design requirement, because a wrong number in this domain (especially once disaster-alert features exist) has real consequences.

**Non-goals for this build:**
- Not running our own NWP model (WRF, etc.) — we consume existing forecast data
- Not integrating IMD/NDMA/Sachet disaster alert systems yet (flagged as a future phase — genuinely separate integration work, not a trivial extension)
- Not building crop-specific agronomic advice — flag any such answer as general, unverified guidance, not a modeled recommendation
- Not building native mobile apps — web-based client for this phase

---

## 2. System Architecture

### 2.1 High-level flow

```
User (voice or text)
      │
      ▼
[Speech-to-Text]  (Web Speech API, browser-side)
      │
      ▼
[Router]  — classifies query as FAST or SLOW
      │
      ├── FAST PATH ─────────────────────────────┐
      │   1. Extract location/parameter           │
      │      (lightweight, rule-based or           │
      │      small-model function call)            │
      │   2. Fetch data (Open-Meteo)                │
      │   3. Validate response (non-null,           │
      │      in-range values)                       │
      │   4. Slot values into a response template   │
      │                                              │
      └── SLOW PATH ─────────────────────────────┐  │
          1. LLM Stage 1 (Generator):              │  │
             - Receives user query                 │  │
             - Calls Open-Meteo as a tool           │  │
             - Drafts a natural-language answer     │  │
             grounded in the tool result             │  │
          2. LLM Stage 2 (Verifier):                │  │
             - Receives draft answer + raw data      │  │
             - Confirms every claim/number is         │  │
             present in the source data                │  │
             - On mismatch: reject and trigger retry    │  │
             or fallback to raw-data template            │  │
                                                          │  │
      ◄─────────────────────────────────────────────────┘──┘
      │
      ▼
[Text-to-Speech]  (Web Speech API, browser-side)
      │
      ▼
User
```

### 2.2 Design principles (do not violate these while implementing)

1. **No ungrounded numbers.** Any numeric weather value in a response must originate from an Open-Meteo API call made during that request — never from a model's own generation.
2. **The verifier is independent.** Its only input is the draft answer text and the raw source data — it does not regenerate an answer, it checks one.
3. **Fail loud, not silent.** If Open-Meteo is unreachable, if the LLM API errors, or if verification fails and retry also fails — the user gets an honest "I couldn't get reliable data right now" response, never a guessed answer.
4. **Cache before you call.** Any repeated location+parameter lookup within the cache TTL window is served from cache, not a fresh API call.
5. **Persist, don't just log to console.** Query history, verification outcomes, and cached forecasts must survive a process restart — this is a durability requirement, not just an observability one (see Section 8a, Persistence Layer).

---

## 3. Query Router

**Purpose:** decide FAST vs SLOW path before any LLM generation call is made, to avoid paying for full generation on simple lookups.

**Implementation:** start with a rule-based classifier (keyword/pattern matching), not an LLM call — the routing decision itself must be cheap and fast, or it defeats its own purpose.

- **FAST path triggers:** direct factual asks — current conditions, single-day forecast, simple yes/no precipitation questions. Example patterns: "what is the weather", "will it rain", "temperature in", "is it sunny".
- **SLOW path triggers:** anything requiring reasoning or synthesis — comparisons, impact/recommendation questions, multi-day analysis, anything with words like "affect", "should I", "compare", "recommend", "why".
- **Default:** if the router is not confident, default to SLOW path — false negatives here (a simple question incorrectly given full reasoning) are cheap; false positives (a complex question given a shallow templated answer) are a worse user experience.
- Log every routing decision (query text + chosen path) for later tuning — this classifier will need iteration once real query patterns are seen.

---

## 4. Fast Path Detail

1. Extract location (required) and parameter (temperature/precipitation/wind/general — default to general if unclear) from the query text.
2. Geocode the location via Open-Meteo's geocoding endpoint.
3. Fetch current + short-range forecast data for that location.
4. Validate: confirm the response contains non-null values for the requested parameter and that values are within physically plausible ranges (basic sanity bounds — e.g., temperature between -60°C and 60°C) as a defense against malformed upstream data.
5. If validation fails, do not fall back to guessing — return an explicit error/retry state.
6. Fill a pre-written response template with the real values. No generation LLM call on this path.

---

## 5. Slow Path Detail

### 5.1 LLM Stage 1 — Generator

- Input: user's query (text, already transcribed if from voice)
- Must call the weather-fetch function/tool mid-turn to get real data before producing any numeric claim — this must be enforced via tool-use/function-calling, not just instructed via prompt (prompted-only grounding is not sufficient given the no-hallucination requirement)
- Output: natural-language draft answer + the raw tool-call result, both passed forward to Stage 2 (return structured output, not just the final text, so Stage 2 and any logging/debugging has access to what data was actually used)

### 5.2 LLM Stage 2 — Verifier

- Input: the draft answer text + the raw source data from Stage 1's tool call (not re-fetched — verify against the exact data Stage 1 used)
- Task: identify every numeric/factual claim in the draft and confirm it is present in (or a reasonable derivation of) the source data
- Output: pass/fail + on fail, which specific claim did not match
- On fail: retry Stage 1 once with the mismatch flagged, or fall back to the fast-path template if retry also fails — never surface an unverified answer to the user
- Implementation note: a programmatic number-extraction-and-diff check should run alongside or before the LLM verification pass — this is cheaper and more reliable for pure number-matching than relying on LLM judgment alone; reserve the LLM verifier for claims that aren't simple number matches (e.g., qualitative statements like "unusually warm for this time of year")

### 5.3 Provider assignment

Two providers are in use (Gemini + Claude). Both a Gemini-generates/Claude-verifies split and a Claude-generates/Gemini-verifies split were discussed — **this is not yet finalized.** Implement the provider call behind a clean interface (e.g., a `LLMClient` abstraction with `generate()` and `verify()` methods) so the specific provider behind each role can be swapped without touching the pipeline logic. Do not hardcode a provider SDK call directly into the pipeline code.

---

## 6. Data Layer — Open-Meteo Integration

- Geocoding endpoint: resolve a place name to latitude/longitude
- Forecast endpoint: current conditions + hourly/daily forecast, pulling temperature, precipitation, wind, and general conditions
- No API key required, but implement retry-with-backoff for transient failures — do not treat a single failed request as "no weather data exists," retry before surfacing an error
- **Caching:** cache geocoding results long-term (place names don't move); cache forecast results per location for roughly 10–15 minutes, since weather data doesn't change faster than that and repeated queries for the same place shouldn't re-hit the API
- Out of scope for this phase: raw NOAA GFS/WRF GRIB2 ingestion, MOSDAC satellite data — noted as future integrations, not stubbed out with fake data
- GIS tooling (spatial layers, map-based analysis) is intentionally not part of this phase — it belongs to a dashboard/monitoring use case (smart-city, researcher tooling), not conversational Q&A. Keep the data layer's location handling (lat/long from geocoding) generic enough that a GIS layer could consume it later without rework.
- WIS2.0 (WMO's inter-agency meteorological data exchange) is institutional infrastructure not directly accessible to this project — reference it in documentation/pitch as the aspirational long-term data source a production version would integrate with, not something built against now.

---

## 7. Voice Layer

- Speech-to-text and text-to-speech via the browser's native Web Speech API (`SpeechRecognition` / `SpeechSynthesis`) — no backend voice processing in this phase
- Stream the response to TTS as it's generated rather than waiting for the full answer — start speaking the first available sentence to reduce perceived latency
- English first; language selection should be a configurable parameter in the voice layer, not hardcoded, so multilingual support can be added without restructuring
- Multilingual (Bhashini ASR/NMT/TTS chain) is a planned future phase — build the voice-layer interface so a Bhashini-backed implementation can be substituted for Web Speech API later without changing the pipeline it feeds into

---

## 8. Backend Structure

```
weathergpt/
├── backend/
│   ├── main.py                    # FastAPI app, route definitions
│   ├── config.py                  # env var loading, API key management
│   │
│   ├── router/
│   │   └── query_classifier.py    # FAST vs SLOW path decision
│   │
│   ├── services/
│   │   ├── weather.py             # Open-Meteo fetch, geocoding, caching
│   │   ├── fast_path.py           # template-based response assembly
│   │   ├── llm_client.py          # provider-agnostic interface: generate(), verify()
│   │   ├── generator.py           # Stage 1 — drafts answer via tool-call
│   │   └── verifier.py            # Stage 2 — independent check + number-diff
│   │
│   ├── models/
│   │   └── schemas.py             # Pydantic models: Query, WeatherData,
│   │                               #   DraftAnswer, VerificationResult, FinalAnswer
│   │
│   ├── db/
│   │   ├── models.py               # SQLAlchemy models: QueryLog, VerificationLog,
│   │   │                            #   CachedForecast, UserPreference
│   │   ├── session.py               # DB connection/session management
│   │   └── migrations/              # Alembic migration scripts
│   │
│   ├── cache/
│   │   └── store.py               # TTL cache — in-memory for local dev, backed by
│   │                               #   Postgres cached_forecast table for persistence
│   │                               #   across restarts (see Section 8a)
│   │
│   ├── logging_config.py          # structured logging: routing decisions,
│   │                               #   verification pass/fail, latency per stage
│   │
│   ├── tests/
│   │   ├── test_weather.py
│   │   ├── test_router.py
│   │   ├── test_generator.py
│   │   ├── test_verifier.py
│   │   └── test_db.py
│   │
│   ├── Dockerfile
│   └── requirements.txt
│
├── frontend/
│   ├── index.html
│   ├── app.js                     # mic capture, SpeechRecognition/Synthesis,
│   │                               #   calls to backend, "thinking" state UI
│   └── style.css
│
├── docker-compose.yml              # backend + postgres, one command local startup
├── .env.example                    # documents required keys without real values
└── README.md
```

### 8a. Persistence Layer (PostgreSQL)

Relational structure fits this project's data (structured logs and key-value-shaped cache entries, not free-form documents), so **PostgreSQL**, not MongoDB.

Minimum tables:
- `query_log` — query text, chosen path (fast/slow), timestamp, latency per stage, final answer, verified flag
- `verification_log` — draft answer, source data snapshot, pass/fail, mismatch detail on failure
- `cached_forecast` — location, parameter, fetched data, fetched-at timestamp, expires-at (backs the cache layer so it survives restarts, not just an in-memory dict)
- `user_preference` — (forward-looking, minimal for now) language preference, default location, once accounts/sessions exist

This is what makes Section 11's logging requirement actually durable — logs written only to console/stdout are lost on restart, which defeats the purpose of collecting them for the open decisions in Section 14.

### 8b. Containerization (Docker)

- `backend/Dockerfile` — containerize the FastAPI app
- `docker-compose.yml` at the root — backend service + Postgres service, so the whole stack starts with one command in any environment (judge's machine, teammate's laptop, eventual deployment)
- Kubernetes is explicitly **not** part of this phase — it's orchestration for scaling multiple instances under real load, which doesn't apply at prototype/demo scale. Note it as a deployment roadmap item, not something to build now.

---

## 9. API Contract (backend ↔ frontend)

**POST /query**

Request:
```json
{
  "text": "will it rain in Bhopal this weekend",
  "input_mode": "voice" | "text"
}
```

Response:
```json
{
  "answer": "string — the final, verified natural-language answer",
  "path": "fast" | "slow",
  "verified": true,
  "source_data": { "...raw Open-Meteo values used..." },
  "latency_ms": 1234
}
```

On failure (data unreachable, verification failed after retry):
```json
{
  "error": "string — honest explanation",
  "answer": null
}
```
The frontend must handle this explicitly — display/speak an honest fallback message, never silently show nothing.

---

## 10. Environment & Configuration

`.env` (not committed — `.env.example` documents required keys):
```
ANTHROPIC_API_KEY=
GEMINI_API_KEY=
DATABASE_URL=postgresql://user:password@localhost:5432/weathergpt
CACHE_TTL_SECONDS=900
LOG_LEVEL=info
```
No key required for Open-Meteo.

---

## 11. Logging & Observability

Log, at minimum, per request:
- Query text and chosen path (fast/slow)
- Latency broken down per stage (data fetch, generation, verification) — this is needed to actually answer the open latency question, not guess at it
- Verification pass/fail, and on fail, what mismatched
- Cache hit/miss on weather data lookups

This isn't optional instrumentation — several open decisions (provider split, whether the 2-stage design is fast enough) depend on this data existing once the system runs.

---

## 12. Testing Requirements

- Unit tests for the router's classification on a representative set of fast/slow example queries
- Unit tests for the weather service against both real and mocked Open-Meteo responses, including malformed/error responses
- Unit tests for the verifier: feed it a draft answer with a deliberately wrong number and confirm it catches the mismatch
- Integration test for the full pipeline on at least: one simple fast-path query, one slow-path reasoning query, one query for an invalid/unresolvable location

---

## 13. Explicitly Out of Scope (do not build, do not stub with fake behavior)

- IMD/NDMA/Sachet disaster alert integration
- Raw NOAA GFS/WRF GRIB2 ingestion
- MOSDAC satellite data integration
- Crop-calendar-grounded agronomic advice (if the slow path is asked something like this, it should answer with an explicit "general guidance, not a verified agronomic recommendation" caveat, not attempt to fake groundedness)
- WhatsApp/IVR/SMS delivery channels
- Bhashini multilingual pipeline (build the voice-layer interface to allow it later; do not implement it now)

---

## 14. Open Decisions Not Yet Finalized

Implementer should flag these rather than silently picking one:
1. Which provider (Gemini or Claude) runs Stage 1 (generator) vs. Stage 2 (verifier)
2. Exact latency budget/threshold that would trigger further optimization (caching more aggressively, further reducing stages)
3. Confidence threshold for the router's fast/slow classification once real query logs exist

---

## 15. Suggested Technology Stack — Alignment

The problem statement names a specific suggested stack. Reconciled against this spec:

| Suggested | Status here | Why |
|---|---|---|
| Python / FastAPI | Used | Core backend |
| Node.js | Not used | FastAPI covers the same role; running both adds no value |
| LLMs (OpenAI/Llama/Gemini, etc.) | Used | Gemini + Claude, per Section 5.3 |
| GIS tools | Deferred | Belongs to a future dashboard/monitoring use case, not conversational Q&A — see Section 6 note |
| Weather APIs | Used | Open-Meteo |
| PostgreSQL / MongoDB | Used — PostgreSQL | Structured, relational data (logs, cache entries) fits Postgres; see Section 8a |
| Docker / Kubernetes | Docker used; Kubernetes deferred | Docker containerizes the stack now (Section 8b); Kubernetes is scaling infrastructure for later, not needed at this stage |
| MQTT / WebSocket | Deferred to V3 (disaster alerts) | Current design is pull-based (user asks, system answers). Push-based delivery — a server proactively notifying a client — is exactly what's needed once disaster/alert dissemination is built, not for the conversational core. Architect the notification boundary so a WebSocket/MQTT layer can be added there without restructuring the query pipeline. |
| WIS2.0 | Referenced, not integrated | WMO's inter-agency data exchange is institutional infrastructure not directly accessible for this build — documented as the long-term aspirational data source (see Section 6 note), not something implemented now |
