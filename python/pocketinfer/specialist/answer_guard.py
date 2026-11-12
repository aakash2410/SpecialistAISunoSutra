"""Fast checks the entailment model misses, run on sentences it has already accepted.

The answer check reads whether a passage supports a sentence. It is weakest on a sentence
that reuses the passage's words with one fact flipped, and it does not look at the question
at all. Each check here is cheap and deterministic, and each only ever removes a sentence.
"""

from __future__ import annotations

import re
from typing import Iterable, Set

from .grounding import _STOPWORDS, _content_terms

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


def unseen_name(sentence: str, passage: str, allowed: Iterable[str] = ()) -> bool:
    """A capitalised word after the sentence start that the passage never uses.

    ``allowed`` is for examples only: names, in lower case, from the teacher's own everyday
    examples (teacher_examples.names_in), which the children know. Answers never pass it.
    """
    source = _words(passage) | set(allowed)
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


# Words that credit someone with making or finding something.
_CREDIT = re.compile(r"^(?:invent|discover)\w*$")
# A credit word right after one of these does not name who made the thing: "the carbon
# transmitter, which facilitated the invention of the telephone" does not say who invented
# the telephone, and "before the invention of paper" says nothing about who invented paper.
_NOT_CREDIT = re.compile(r"^(?:facilitat|help|led$|lead|enabl|allow|paved$|contribut|aided$|possibl|"
                         r"before$|after$|since$|until$|prior$|tried$|tries$|try|attempt)")
# A clause about something else starts at one of these, so the credit word does not reach past it.
_CLAUSE = {"which", "that", "who", "whose", "where", "when", "while", "but", "though", "although", "because"}
_PRONOUNS = {"he", "she", "his", "her"}
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'“‘(])")
_FUNCTION = {"the", "this", "that", "these", "those", "it", "its", "he", "she", "they", "we", "in", "on", "a",
             "an", "when", "after", "before", "during", "many", "some", "most", "one", "today", "later"}
# How far a credit word reaches: forward to what was made ("invented the electric bulb, the
# gramophone and the carbon transmitter"), back to it in the passive ("plastic was invented").
_AFTER, _BEFORE = 20, 6
# Words that say nothing about what was made.
_NOT_MADE = _STOPWORDS | {"was", "were", "by", "and", "first", "credit", "given", "usually", "called", "named",
                          "famous", "great", "scientist", "year", "years", "man", "woman", "person", "people"}


def _fold(word: str) -> str:
    return re.sub(r"(?:es|s)$", "", word) if len(word) > 4 else word


def _credited(tokens: list, index: int) -> list:
    """The words a credit word at ``index`` is about, up to the nearest clause boundary."""
    out = []
    for step, limit in ((1, _AFTER), (-1, _BEFORE)):
        j = index + step
        while 0 <= j < len(tokens) and abs(j - index) <= limit and tokens[j] not in _CLAUSE:
            out.append(tokens[j])
            j += step
    return out


def _names(sentence: str, passage: str) -> Set[str]:
    """Capitalised words that name someone. A first word counts unless the passage uses it in
    lower case, as it does "scientists"."""
    words = re.findall(r"\b[A-Z][a-z]{2,}\b", sentence)
    lower = set(_TOKEN.findall(passage))   # the passage as written, so capitals stay distinct
    first = sentence.split(" ", 1)[0].strip("\"'“‘(")
    return {w.lower() for w in words
            if w.lower() not in _NOT_NAMES and w.lower() not in _FUNCTION
            and not (w == first and w.lower() in lower)}


def unsupported_credit(sentence: str, passage: str) -> bool:
    """The sentence says someone invented or discovered something, and no passage sentence
    gives them that credit.

    The passage must have a sentence with the person (or "he"/"she", when the person is named
    elsewhere in it), a credit word that is not just "helped" or "led to" an invention, and at
    least half of what the answer says was made within that credit word's clause. A passage
    with "Edison" and "facilitated the invention of the telephone" fails it, so "Edison invented
    the telephone" is dropped. Only sentences that name someone are checked.
    """
    said = _TOKEN.findall(sentence.lower())
    marks = [i for i, w in enumerate(said) if _CREDIT.match(w)]
    if not marks:
        return False
    names = _names(sentence, passage)
    if not names:
        return False
    made = {_fold(w) for i in marks for w in _credited(said, i)
            if w not in names and len(w) >= 3 and not _CREDIT.match(w) and w not in _NOT_MADE}
    if not made:
        return False
    named_anywhere = bool(names & set(_TOKEN.findall(passage.lower())))
    for part in _SENTENCE.split(passage):
        tokens = _TOKEN.findall(part.lower())
        if not (names & set(tokens) or (named_anywhere and _PRONOUNS & set(tokens))):
            continue
        for i, w in enumerate(tokens):
            if not _CREDIT.match(w) or any(_NOT_CREDIT.match(t) or t in _NEGATORS for t in tokens[max(0, i - 3):i]):
                continue
            found = made & {_fold(t) for t in _credited(tokens, i)}
            if 2 * len(found) >= len(made):
                return False
    return True


def contradicts(sentence: str, passages: Iterable[str], allowed_names: Iterable[str] = ()) -> bool:
    """True when the sentence flips a fact against every passage it could have come from.

    ``allowed_names`` goes to unseen_name, for examples only (see there).
    """
    allowed_names = set(allowed_names)
    checks = (wrong_number, flipped_opposite, reversed_direction,
              lambda s, p: unseen_name(s, p, allowed_names), dropped_negation, unsupported_credit)
    passages = list(passages)
    return bool(passages) and all(any(c(sentence, p) for c in checks) for p in passages)


def shares_topic(question: str, sentence: str) -> bool:
    return bool(_content_terms(question) & _content_terms(sentence))
