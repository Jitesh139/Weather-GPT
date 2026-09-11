"""Router classification tests (spec Section 12) on representative
fast/slow example queries, plus the default-to-slow-when-unsure rule.
"""
import pytest

from router.query_classifier import classify

FAST_QUERIES = [
    "what is the weather in Delhi",
    "will it rain tomorrow in Pune",
    "temperature in Chennai",
    "is it sunny in Bangalore",
    "what's the weather in Kolkata",
    "forecast for Jaipur",
    "how hot is it in Nagpur",
    "humidity in Lucknow",
]

SLOW_QUERIES = [
    "how will this rain affect my flight tomorrow",
    "should I carry an umbrella and why",
    "compare Mumbai and Delhi weather this week",
    "recommend the best day to travel to Goa this weekend",
    "why is it so humid in Chennai right now",
    "what's your advice for planning a picnic this weekend",
    "plan my trip to Manali next week",
]

AMBIGUOUS_DEFAULT_SLOW = [
    "weather",
    "hmm",
]


@pytest.mark.parametrize("query", FAST_QUERIES)
def test_fast_queries_classified_fast(query):
    assert classify(query) == "fast"


@pytest.mark.parametrize("query", SLOW_QUERIES)
def test_slow_queries_classified_slow(query):
    assert classify(query) == "slow"


@pytest.mark.parametrize("query", AMBIGUOUS_DEFAULT_SLOW)
def test_ambiguous_queries_default_to_slow(query):
    assert classify(query) == "slow"


def test_slow_keyword_wins_even_with_fast_signal_present():
    # Contains "weather in" (a FAST pattern) but also "should i" - the
    # spec's rule is that reasoning/synthesis words should win.
    assert classify("should i worry about the weather in Delhi this week") == "slow"
