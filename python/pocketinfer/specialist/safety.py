"""Safety check on the request, before retrieval.

Refusals alone are not safe: a child disclosing abuse should not hear "that is not in
the textbook", and as the corpus grows, topics stop being refused by accident. So every
request is checked here first, and a flagged one gets a fixed, human-written response.
The answer model never generates text for these.

Three steps, in order:
  * Patterns catch plain statements of intent, without touching the curriculum.
  * A general knowledge question with nothing personal in it stops here, whether it is
    asked as "What was..." or as "Tell me about...". The NLI reads
    questions about war, caste or the body as a person in danger, and a question like
    this cannot be a disclosure. It still only gets an answer from the textbook, or a
    refusal, so it costs little if one gets through.
  * Everything else goes to an NLI model fine-tuned for this job, which is asked whether
    the request matches each concern compared with a set of safe labels.

It is a separate model from the answer verifier, because fine-tuning one model for both
jobs caught more unsafe requests but blocked more good answers. If it is missing this
raises rather than letting requests through unchecked.

DRAFT WORDING: the responses and helpline numbers need sign-off from someone with
safeguarding experience before this reaches a classroom.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional

from ..models.verify import Verifier, _resolve_dir

_DIR_ENV = "SPECIALIST_SAFETY_DIR"
_DIRNAME = "nli-safety-onnx"

# An entailment-minus-contradiction margin. Scores near this value barely change the
# result on the fine-tuned model, so it stays where it was first tuned.
NLI_MARGIN = -1.07
# How far a medical concern must lead the others before the request gets the medical reply.
# Below this it gets the general reply, which also gives the Childline number.
MEDICAL_LEAD = 0.75

_PATTERNS = {
    "self_harm": r"\b(kill|hurt|harm|cut|hang|poison)(ing)? (myself|himself|herself|themselves)\b"
                 r"|\bsuicid|\b(want|wants|wish|wishes|going|plan\w*) to (die|end (my|his|her|their) life)\b"
                 r"|\bend (my|his|her|their) life\b|\bdo(n't| not) want to live\b|\bno reason to live\b"
                 r"|\bjump (off|from)\b.*\b(roof|building|bridge)\b|\btoo many (tablets|pills)\b"
                 r"|\b(nobody|no one) would care\b|\bwant (it|this|everything) to end\b"
                 r"|\bswallow\b.*\b(pills|tablets)\b",
    "abuse": r"\b(touch|touches|touched|touching)\b.*\b(me|her|him|my body|private|bad way)\b|\bbad touch\b"
             r"|\b(beat|beats|beating|hit|hits|hurt|hurts|slap|slaps|kick|kicks|burn|burns) (me|him|her)\b"
             r"|\bprivate (photos|pictures|parts)\b|\bforced? to marry\b|\block(s|ed)? (me|him|her)\b"
             r"|\bmolest|\brape[ds]?\b|\bharass"
             r"|\bi(?:'m| am)\b.*\b(scared|afraid|frightened|terrified)\b",
    "violence": r"\b(make|making|build|building|get|buy|create)\b.*\b(bomb|explosives?|gun|pistol|weapon|poison)\b"
                r"|\b(hurt|kill|stab|beat up|attack|poison)\b (someone|somebody|a person|my|him|her|them|a classmate)\b"
                r"|\bat someone\b|\bsomeone's food\b|\bsteal\b|\bbreak into\b|\bshoplift"
                r"|\bset\b.*\bon fire\b|\bburn (down|(a|the) (house|building|school))\b",
    "adult": r"\bporn|\bsex\b(?! (ratio|determination|organs?|cells?|hormones?))|\bnaked\b|\bnude\b"
             r"|\bdirty (story|joke|picture|video)|\b(adult|blue) (video|film|movie|content)|\berotic",
    "hate": r"\b(caste|religion|community|race|tribe|tribal|muslims?|hindus?|christians?|sikhs?|dalits?|girls?|boys?|women|men)\b"
            r".*\b(worst|worse than|inferior|bad people|dumb|stupid|lowest|smarter than|should not (go|be educated)|should leave)\b"
            r"|\b(worst|lowest)\b.*\b(caste|religion)\b|\bless intelligent\b|\binsults?\b",
    "medical": r"\b(which|what) (medicine|drug|tablets?|pills?|dose)\b|\bhow much (paracetamol|medicine)\b"
               r"|\b(should|can) i (take|give|stop)\b.*\b(medicine|tablets?|drug|dose|paracetamol)\b"
               r"|\btreat\b.*\bat home\b|\bstop\b.*\bbleeding\b|\bsnake ?bite\b",
}
_COMPILED = {name: re.compile(p, re.IGNORECASE) for name, p in _PATTERNS.items()}

_GENERAL_QUESTION = re.compile(
    r"^(what (is|are|was|were|does|did|happens|happened|caused)|why (did|do|does|is|was|were)"
    r"|how (did|do|does|is|are|was|were)|who (is|was|were|did|wrote|built|killed)"
    r"|(when|where) (is|was|were|did))\b",
    re.IGNORECASE,
)
# "Tell me about the Mughals" asks for knowledge the same way "What were the Mughals?" does.
# The "me" in the request belongs to the asking, so only the rest of the sentence is checked
# for personal words. "Tell me about my uncle touching me" still goes on to the model.
_REQUEST = re.compile(
    r"^(?:please\s+)?(?:(?:can|could|will|would)\s+you\s+)?(?:please\s+)?"
    r"(?:tell\s+me|explain|describe|teach\s+me)"
    r"(?:\s+(?:something|more|a\s+little|a\s+bit))?(?:\s+(?:about|on|regarding))?\s+",
    re.IGNORECASE,
)
_HOW_TO = re.compile(r"how\s+to\b|(?:how|what|where)\s+(?:can|could|should|do|would)\s+(?:i|we|you|one|someone)\b",
                     re.IGNORECASE)
_PERSONAL = re.compile(
    r"\b(i|i'm|i've|me|my|mine|myself|our|us|someone|somebody)\b"
    r"|\b(best|easiest|quickest|fastest|safest)\b.*\b(way|to)\b",
    re.IGNORECASE,
)

_CONCERNS = [
    "The speaker wants to hurt, kill or steal.",
    "The speaker wants to make a weapon, bomb or poison.",
    "The speaker wants to die or hurt themselves.",
    "A person is thinking about suicide.",
    "A child is being beaten or abused.",
    "A child is being touched in a sexual way.",
    "The speaker is asking about sex or pornography.",
    "The speaker says one group of people is worse than another.",
]
_MEDICAL_CONCERNS = [
    "The speaker asks which medicine to take.",
    "The speaker is asking what to do about an illness or injury.",
]
_SAFE_LABELS = [
    "The speaker is asking about a school subject.",
    "The speaker is asking how something works.",
    "The speaker is asking a question about science, maths, history or language.",
]

# Which fixed response each category gets. Anything else flagged only by the NLI gets the
# unsure message, because the NLI cannot tell which kind of concern it found and it does
# flag some harmless requests.
_RESPONSE_FOR = {"self_harm": "self_harm", "abuse": "care", "concern": "unsure", "medical": "medical",
                 "violence": "decline", "adult": "decline", "hate": "decline"}

RESPONSES: Dict[str, Dict[str, str]] = {
    "self_harm": {
        "en": "I am sorry you are going through this. Please talk to a trusted adult now. "
              "You can call Tele-MANAS on 14416 or Childline on 1098, for free, at any time.",
        "hi": "मुझे दुख है कि आप इससे गुज़र रहे हैं। कृपया अभी किसी भरोसेमंद बड़े से बात करें। "
              "आप टेली-मानस को 14416 पर या चाइल्डलाइन को 1098 पर, किसी भी समय, मुफ़्त में फ़ोन कर सकते हैं।",
    },
    "care": {
        "en": "This sounds serious, and it is right to speak up. Please tell a trusted adult now. "
              "If a child is being hurt or feels unsafe, call Childline on 1098, for free, at any time.",
        "hi": "यह गंभीर लगता है, और इसके बारे में बताना सही है। कृपया अभी किसी भरोसेमंद बड़े को बताएँ। "
              "अगर किसी बच्चे को चोट पहुँचाई जा रही है या वह असुरक्षित है, तो चाइल्डलाइन को 1098 पर, "
              "किसी भी समय, मुफ़्त में फ़ोन करें।",
    },
    "unsure": {
        "en": "I cannot answer this here. Please ask your teacher. If someone is hurting you "
              "or you feel unsafe, call Childline on 1098, for free, at any time.",
        "hi": "मैं यहाँ इसका उत्तर नहीं दे सकता। कृपया अपने शिक्षक से पूछें। अगर कोई आपको चोट पहुँचा रहा है "
              "या आप असुरक्षित महसूस करते हैं, तो चाइल्डलाइन को 1098 पर, किसी भी समय, मुफ़्त में फ़ोन करें।",
    },
    "medical": {
        "en": "I cannot give medical advice. Please ask a doctor or health worker now. "
              "In an emergency, call 112.",
        "hi": "मैं चिकित्सा सलाह नहीं दे सकता। कृपया अभी किसी डॉक्टर या स्वास्थ्य कार्यकर्ता से पूछें। "
              "आपात स्थिति में 112 पर फ़ोन करें।",
    },
    "decline": {
        "en": "I cannot help with that. Please ask me about your school subjects.",
        "hi": "मैं इसमें मदद नहीं कर सकता। कृपया मुझसे अपने स्कूल के विषयों के बारे में पूछें।",
    },
}


@dataclass
class SafetyFlag:
    category: str
    source: str             # "pattern" or "nli"
    response_en: str
    response_hi: str


def load_safety_model() -> Verifier:
    found = _resolve_dir(env=_DIR_ENV, dirname=_DIRNAME)
    if not found:
        raise RuntimeError(
            f"Safety model not found. Put it in local/models/{_DIRNAME} or set {_DIR_ENV} "
            "to a dir with model.onnx + tokenizer.json + config.json."
        )
    return Verifier(found)


class SafetyCheck:
    def __init__(self, verifier: Verifier):
        self.verifier = verifier

    def _nli_category(self, request: str) -> Optional[str]:
        def best(hypotheses):
            return max(self.verifier.entailment_margin(request, h) for h in hypotheses)
        other, medical = best(_CONCERNS), best(_MEDICAL_CONCERNS)
        if max(other, medical) - best(_SAFE_LABELS) < NLI_MARGIN:
            return None
        return "medical" if medical - other >= MEDICAL_LEAD else "concern"

    def check(self, request: str) -> Optional[SafetyFlag]:
        """Return a SafetyFlag if this English request should not reach retrieval."""
        for category, pattern in _COMPILED.items():
            if pattern.search(request):
                return self._flag(category, "pattern")
        if _GENERAL_QUESTION.search(request.strip()) and not _PERSONAL.search(request):
            return None
        asked = _REQUEST.match(request.strip())
        if asked:
            rest = request.strip()[asked.end():]
            # Asking how to do something is a request for instructions, not knowledge,
            # so "Explain how to..." still goes to the model.
            if rest and not _PERSONAL.search(rest) and not _HOW_TO.match(rest):
                return None
        category = self._nli_category(request)
        return self._flag(category, "nli") if category else None

    @staticmethod
    def _flag(category: str, source: str) -> SafetyFlag:
        text = RESPONSES[_RESPONSE_FOR[category]]
        return SafetyFlag(category, source, text["en"], text["hi"])
