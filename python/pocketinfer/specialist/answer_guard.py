"""Fast checks the entailment model misses, run on sentences it has already accepted.

The answer check reads whether a passage supports a sentence. It is weakest on a sentence
that reuses the passage's words with one fact flipped, and it does not look at the question
at all. Each check here is cheap and deterministic, and each only ever removes a sentence.
"""

from __future__ import annotations

import re
from typing import Iterable, Set

from .grounding import _content_terms

# Below this e5 similarity, the first sentence of an answer is not answering the question.
# On the dev set this dropped 23 of 31 off-question sentences and 3 of 59 on-question ones.
QUESTION_SIMILARITY = 0.82

# Words that flip a fact. A sentence fails when it uses one side and the passage uses only
# the other, so a passage that mentions both, or neither, is left alone.
OPPOSITES = [
    ("hot", "cold"), ("hotter", "colder"), ("warm", "cool"), ("increase", "decrease"), ("increases", "decreases"),
    ("increased", "decreased"), ("rise", "fall"), ("rises", "falls"), ("large", "small"), ("larger", "smaller"),
    ("more", "less"), ("higher", "lower"), ("high", "low"), ("fast", "slow"), ("faster", "slower"),
    ("before", "after"), ("above", "below"), ("first", "last"), ("positive", "negative"), ("acid", "base"),
    ("acidic", "basic"), ("absorb", "release"), ("absorbs", "releases"), ("gain", "lose"), ("gains", "loses"),
    ("north", "south"), ("east", "west"), ("add", "subtract"), ("erect", "inverted"), ("erect", "upside"),
    ("upward", "downward"), ("inside", "outside"), ("day", "night"), ("towards", "away"), ("sum", "difference"),
    ("many", "few"), ("strong", "weak"), ("rich", "poor"), ("connected", "unconnected"),
    ("attract", "repel"), ("attracts", "repels"), ("expand", "contract"), ("expands", "contracts"),
    ("solid", "liquid"), ("male", "female"), ("always", "never"), ("true", "false"),
]
_WORD = re.compile(r"[a-z]+")
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*(?![\w])")
_NUMBER_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
                 "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "twenty": "20",
                 "thirty": "30", "forty": "40", "fifty": "50", "hundred": "100", "thousand": "1000"}
_NAME = re.compile(r"\b[A-Z][a-z]{2,}\b")
_NOT_NAMES = {"the", "this", "that", "these", "those", "they", "when", "for", "india", "indian", "earth"}
_TOKEN = re.compile(r"[a-z']+")
_NEGATORS = {"not", "never", "no", "cannot", "can't", "don't", "doesn't", "didn't", "isn't", "aren't", "won't", "without"}
_FROM_TO = re.compile(r"\bfrom\s+(?:the\s+)?([a-z]+)\s+to\s+(?:the\s+)?([a-z]+)", re.IGNORECASE)


def _words(text: str) -> Set[str]:
    return set(_WORD.findall(text.lower()))


def _numbers(text: str) -> Set[str]:
    found = {n.replace(",", "") for n in _NUMBER.findall(text)}
    return found | {_NUMBER_WORDS[w] for w in _words(text) if w in _NUMBER_WORDS}


def wrong_number(sentence: str, passage: str) -> bool:
    """A digit in the sentence that the passage never gives, in digits or words."""
    stated = {n.replace(",", "") for n in _NUMBER.findall(sentence)}
    return bool(stated - _numbers(passage))


def flipped_opposite(sentence: str, passage: str) -> bool:
    said, source = _words(sentence), _words(passage)
    for a, b in OPPOSITES:
        if (a in said and b not in said and b in source and a not in source) or \
           (b in said and a not in said and a in source and b not in source):
            return True
    return False


def reversed_direction(sentence: str, passage: str) -> bool:
    """"From X to Y" in the sentence, where the passage only says "from Y to X"."""
    source = {(a.lower(), b.lower()) for a, b in _FROM_TO.findall(passage)}
    return any((b.lower(), a.lower()) in source and (a.lower(), b.lower()) not in source
               for a, b in _FROM_TO.findall(sentence))


def unseen_name(sentence: str, passage: str) -> bool:
    """A capitalised word after the sentence start that the passage never uses."""
    source = _words(passage)
    return any(w.lower() not in source for w in _NAME.findall(sentence.split(" ", 1)[-1]) if w.lower() not in _NOT_NAMES)


def dropped_negation(sentence: str, passage: str) -> bool:
    """The sentence states plainly what the passage only ever says with "not" or "never"."""
    tokens = _TOKEN.findall(sentence.lower())
    source = _TOKEN.findall(passage.lower())
    for i, word in enumerate(tokens):
        if len(word) < 4 or word in _NEGATORS or set(tokens[max(0, i - 3):i]) & _NEGATORS:
            continue
        places = [j for j, w in enumerate(source) if w == word]
        if places and all(j and source[j - 1] in _NEGATORS for j in places):
            return True
    return False


def contradicts(sentence: str, passages: Iterable[str]) -> bool:
    """True when the sentence flips a fact against every passage it could have come from."""
    checks = (wrong_number, flipped_opposite, reversed_direction, unseen_name, dropped_negation)
    passages = list(passages)
    return bool(passages) and all(any(c(sentence, p) for c in checks) for p in passages)


def shares_topic(question: str, sentence: str) -> bool:
    return bool(_content_terms(question) & _content_terms(sentence))
