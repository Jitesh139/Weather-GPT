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


def classify_with_reason(query: str) -> tuple[Path, str]:
    text = query.strip().lower()

    for kw in SLOW_KEYWORDS:
        if kw in text:
            return "slow", f"slow_keyword:{kw}"

    for pattern in FAST_PATTERNS:
        if pattern.search(text):
            return "fast", f"fast_pattern:{pattern.pattern}"

    return "slow", "default_to_slow"


def classify(query: str) -> Path:
    path, _reason = classify_with_reason(query)
    return path
