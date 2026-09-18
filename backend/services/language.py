"""Which language the user asked in, and the user-facing strings that
answer them in it.

Two different mechanisms handle the two paths, on purpose:

- The SLOW path is an LLM, so it doesn't need this module to translate
  anything - it's simply told to reply in the same language and script
  the user wrote in, which works for every language the model speaks.
- The FAST path fills in templates, so it can only answer in a language
  it has templates for. That's the closed set below; anything else falls
  back to English rather than emitting a half-translated answer.

Detection is deliberately crude - a script check plus a keyword list -
because the only decision it drives is which template set to use. Hindi
in particular is frequently typed and voice-transcribed in Latin script
("aaj ka mausam kaisa hai"), which a script check alone would miss
entirely, so the keyword list carries most of the weight in practice.
"""
import re

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ("en", "hi")

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# Common Hindi/Hinglish tokens as people actually type and speak them to
# a weather assistant. Matched as whole words against a lowercased query.
_ROMANIZED_HINDI = {
    "mausam", "mosam", "mausum", "kaisa", "kaisi", "kesa", "kesi", "kaise",
    "aaj", "kal", "parso", "abhi", "barish", "baarish", "barsat", "garmi",
    "sardi", "thand", "thandi", "dhoop", "hawa", "badal", "temperature",
    "hai", "hoga", "rahega", "rahegi", "kya", "kyaa", "batao", "bataye",
    "mein", "mei", "me", "ka", "ki", "ke", "kitna", "kitni", "hogi",
}
# Words that are Hindi-specific enough that one is enough to decide, as
# opposed to "me"/"ka", which appear in English text too.
_STRONG_HINDI = {
    "mausam", "mosam", "mausum", "kaisa", "kaisi", "kesa", "kesi", "kaise",
    "aaj", "barish", "baarish", "barsat", "garmi", "sardi", "thand",
    "batao", "bataye", "rahega", "rahegi", "kitna", "kitni",
}

_WORD = re.compile(r"[a-z]+")


def detect_language(text: str) -> str:
    """Returns a language code from SUPPORTED_LANGUAGES. Never raises -
    an undetectable query is answered in English."""
    if not text:
        return DEFAULT_LANGUAGE
    if _DEVANAGARI.search(text):
        return "hi"

    words = set(_WORD.findall(text.lower()))
    if words & _STRONG_HINDI:
        return "hi"
    # No single strong marker, so require a few weaker ones before
    # calling it Hindi - "me" alone must not flip an English question.
    if len(words & _ROMANIZED_HINDI) >= 3:
        return "hi"
    return DEFAULT_LANGUAGE


# User-facing strings for the FAST path and its failure modes. Keyed by
# message id, then language.
_MESSAGES = {
    "no_location": {
        "en": "I couldn't identify a location in your question - please specify a city.",
        "hi": "मैं आपके सवाल में कोई जगह नहीं पहचान सका - कृपया शहर का नाम बताइए।",
    },
    "unreliable_data": {
        "en": "I couldn't get reliable data right now ({reason}).",
        "hi": "मुझे अभी भरोसेमंद डेटा नहीं मिल सका ({reason})।",
    },
    "fetch_failed": {
        "en": "I couldn't get reliable weather data right now: {error}",
        "hi": "मुझे अभी भरोसेमंद मौसम डेटा नहीं मिल सका: {error}",
    },
    "verify_failed": {
        "en": "I couldn't get a reliable, verified answer right now: {error}",
        "hi": "मुझे अभी कोई भरोसेमंद, जाँचा हुआ जवाब नहीं मिल सका: {error}",
    },
}


def message(message_id: str, language: str, **values) -> str:
    """Localized user-facing message, falling back to English for any
    language without a translation."""
    variants = _MESSAGES[message_id]
    template = variants.get(language, variants[DEFAULT_LANGUAGE])
    return template.format(**values)
