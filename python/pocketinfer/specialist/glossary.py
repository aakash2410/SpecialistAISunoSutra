"""Hindi subject terms the translator gets wrong, fixed before translation.

Translation runs on general Hindi, so a maths word with an everyday meaning loses it:
"भिन्न क्या होता है?" comes back as "What is different?", and the device then answers the
wrong question. Each term here is replaced with its English word, which the translator
carries through. The rules only fire where the school meaning is clear, because "भिन्न"
also really does mean "different", as in "दो भिन्न प्रकार" or "उससे भिन्न".
"""

from __future__ import annotations

import re
from typing import List, Tuple

_HI = "\u0900-\u0963\u0966-\u097F"  # Devanagari, without the danda full stops
_START = rf"(?<![{_HI}])"
_END = rf"(?![{_HI}])"


def _term(hindi: str) -> str:
    return f"{_START}(?:{hindi}){_END}"


# Longest and most specific first, so a phrase is replaced before any word inside it.
_RULES = [(re.compile(pattern), english) for pattern, english in [
    (_term("उचित भिन्न"), "proper fraction"),
    (_term("अनुचित भिन्न"), "improper fraction"),
    (_term("मिश्रित भिन्न|मिश्र भिन्न"), "mixed fraction"),
    (_term("तुल्य भिन्न"), "equivalent fraction"),
    (_term("दशमलव भिन्न"), "decimal fraction"),
    (_term("भिन्नों"), "fractions"),
    # Bare "भिन्न" counts only when what follows treats it as a thing, not a description.
    (rf"(?<!से ){_term('भिन्न')}(?=\s+(?:क्या|का|की|के|को|में|जोड़|घटा|गुणा|भाग|और दशमलव)|\s*[?।]\s*$)", "fraction"),
    (rf"{_START}एक भिन्न(?=\s+(?:है|होता|होती){_END})", "a fraction"),
    (_term("अंश और हर"), "numerator and denominator"),
    (_term("सम संख्या(?:एँ|एं|ओं)?"), "even number"),
    (_term("विषम संख्या(?:एँ|एं|ओं)?"), "odd number"),
    (_term("बहुलक"), "mode"),
]]


def apply_glossary(hindi: str) -> Tuple[str, List[str]]:
    """Replace the subject terms in a Hindi request. Returns the text and the English terms."""
    terms: List[str] = []
    for pattern, english in _RULES:
        hindi, count = pattern.subn(english, hindi)
        if count:
            terms.append(english)
    return hindi, terms


def keep_terms(translation: str, terms: List[str]) -> str:
    """Add back any term the translator dropped, so retrieval still sees the real topic."""
    lowered = translation.lower()
    missing = [t for t in terms if t.lower() not in lowered]
    return f"{translation} ({', '.join(missing)})" if missing else translation
