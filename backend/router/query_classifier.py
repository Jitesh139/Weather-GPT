"""Rule-based FAST/SLOW query router (spec Section 3). Deliberately NOT
an LLM call - the routing decision itself must be cheap, or it defeats
its own purpose of avoiding full generation on simple lookups.

Default: SLOW when unsure. Per spec, a false negative (simple question
given full reasoning) is cheap; a false positive (complex question given
a shallow templated answer) is a worse user experience.
"""
import re
from typing import Literal

Path = Literal["fast", "slow"]

# Checked first - any hit routes to SLOW regardless of FAST keyword matches,
# since reasoning/synthesis words indicate the query needs more than a
# templated lookup even if it also mentions "weather" or a location.
SLOW_KEYWORDS = [
    "affect",
    "should i",
    "should we",
    "compare",
    "recommend",
    "recommendation",
    "why",
    "impact",
    "plan my",
    "plan for",
    "best day",
    "best time",
    "this week",
    "this weekend",
    "next week",
    "advice",
    "suitable for",
]

FAST_PATTERNS = [
    re.compile(r"\bwill it rain\b"),
    re.compile(r"\btemperature (in|at|for)\b"),
    re.compile(r"\bis it (sunny|raining|cloudy|hot|cold|windy)\b"),
    re.compile(r"\bweather (in|at|for)\b"),
    re.compile(r"\bforecast for\b"),
    re.compile(r"\bhow (hot|cold|windy|humid) is\b"),
    re.compile(r"\bwhat('?s| is) the weather\b"),
    re.compile(r"\bhumidity (in|at|for)\b"),
]


# Hindi/Hinglish equivalents of the FAST patterns above. Without these,
# every non-English question fell through to default_to_slow and paid a
# full two-stage LLM round trip - about ten seconds - for a lookup the
# template path answers from cache in milliseconds. Matched against both
# Devanagari and the Latin-script spellings people actually type and that
# speech-to-text returns.
FAST_PATTERNS_HI = [
    re.compile(r"\bmausam\s+(kaisa|kaisi|kesa|kesi|kaise)\b"),
    re.compile(r"\b(kaisa|kaisi|kesa|kesi)\s+(hai|rahega|rahegi|hoga|h)\b"),
    re.compile(r"\b(taapman|tapman|temperature)\s+(kitna|kitni|kya)\b"),
    re.compile(r"\b(barish|baarish|barsat)\s+(hogi|ho\s+rahi|hai)\b"),
    re.compile(r"\b(garmi|sardi|thand|hawa)\s+(kitni|kitna|kaisi|kaisa)\b"),
    re.compile(r"मौसम\s+कैस"),
    re.compile(r"(तापमान|गरमी|गर्मी|बारिश)\s+(कितन|कैस)"),
]

# Reasoning words, Hindi side. Same rule as SLOW_KEYWORDS: a hit here
# beats any FAST match.
SLOW_KEYWORDS_HI = [
    "kyun", "kyu", "kyon", "chahiye", "salah", "behtar", "tulna", "asar",
    "क्यों", "चाहिए", "सलाह", "तुलना", "असर",
]


def classify_with_reason(query: str) -> tuple[Path, str]:
    text = query.strip().lower()

    for kw in SLOW_KEYWORDS:
        if kw in text:
            return "slow", f"slow_keyword:{kw}"

    for kw in SLOW_KEYWORDS_HI:
        if kw in text:
            return "slow", f"slow_keyword_hi:{kw}"

    for pattern in FAST_PATTERNS:
        if pattern.search(text):
            return "fast", f"fast_pattern:{pattern.pattern}"

    for pattern in FAST_PATTERNS_HI:
        if pattern.search(text):
            # Gated on the location actually being extractable, unlike the
            # English patterns. Hindi word order puts the place name after
            # a postposition, which the extractor only sometimes finds - and
            # a wrong FAST route here would answer "please specify a city"
            # to a question the SLOW path could have handled fine.
            from services.fast_path import extract_location_and_param

            location, _ = extract_location_and_param(query)
            if location:
                return "fast", f"fast_pattern_hi:{pattern.pattern}"
            return "slow", "hi_pattern_without_location"

    return "slow", "default_to_slow"


def classify(query: str) -> Path:
    path, _reason = classify_with_reason(query)
    return path
