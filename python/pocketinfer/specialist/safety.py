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

# Something a person does to themselves that can kill or hurt them. On its own it is often a
# lesson ("What happens when you hold your breath?"), so it only counts beside a word for
# doing it on purpose. Fasting for a festival or a protest, animals and sport are left out.
_HARM_ACT = (r"(?:\b(?:stop\w*|quit\w*|refus\w*)\s+(?:eating|drinking|food|water|to\s+eat|to\s+drink|taking\s+(?:\w+\s+){0,2}medicines?)"
             r"|\b(?:not|n't|never)\s+(?:eat|drink)\w*\s+(?:anything|at\s+all|for\s+(?:days|weeks|months|a\s+long\s+time))"
             r"|\bstop\w*\s+(?:their|his|her|my|your)\s+(?:own\s+)?(?:heart|breathing)"
             r"|\b(?:cut|hurt|harm|burn|starv|drown)\w*\s+(?:myself|yourself|himself|herself|themselves|oneself)"
             r"|\b(?:swallow|overdos)\w*"
             r"|\b(?:drink|drank|drinks|drinking)\s+(?:poison|pesticide|phenyl|bleach|kerosene))\b")
_NOT_FASTING = (r"^(?!.*\b(?:fast|fasting|vrat|roza|ramzan|ramadan|navratri|protest|strike|satyagraha|gandhi"
                r"|animals?|birds?|bears?|insects?|fish|snakes?|frogs?|plants?|hibernat\w*|athletes?|players?|wrestlers?)\b).*?")

_PATTERNS = {
    "self_harm": r"\b(kill|hurt|harm|cut|hang|poison)(ing)? (myself|himself|herself|themselves)\b"
                 r"|\bsuicid|\b(want|wants|wish|wishes|going|plan\w*) to (die|end (my|his|her|their) life)\b"
                 r"|\bend (my|his|her|their) life\b|\bdo(n't| not) want to live\b|\bno reason to live\b"
                 r"|\bjump (off|from)\b.*\b(roof|building|bridge)\b|\btoo many (tablets|pills)\b"
                 r"|\b(nobody|no one) would care\b|\bwant (it|this|everything) to end\b"
                 r"|\bswallow\b.*\b(pills|tablets)\b"
                 # Indirect statements: no point in living, wishing not to exist, feeling worthless.
                 r"|\b(?:point|use)\s+(?:of|in)\s+(?:me\s+|my\s+)?(?:being\s+alive|staying\s+alive|going\s+on|my\s+life"
                 r"|living(?!\s+(?:things?|organisms?|beings?|creatures?|room|cells?)))\b"
                 r"|\bwhy\s+(?:should|would)\s+i\b.{0,30}\b(?:alive|live|go\s+on)\b"
                 r"|\b(?:wish|want)\b.{0,25}\b(?:disappear|vanish|never\s+been\s+born|not\s+(?:be\s+)?(?:here|alive))\b"
                 r"|\bwish\s+i\s+(?:would|could)?\s*(?:not|never)\s+wake\b"
                 r"|\bi(?:'m|\s+am|\s+feel)\b.{0,25}\b(?:worthless|hopeless|a\s+burden)\b"
                 r"|\b(?:nobody|no\s+one)\s+(?:needs|wants|loves)\s+me\b|\bno\s+hope\s+(?:left\s+)?for\s+me\b"
                 r"|\btired\s+of\s+(?:being\s+alive|living|my\s+life)\b|\bcan(?:'t|not)\s+go\s+on\b"
                 r"|\b(?:happier|easier|better)(?:\s+off)?\s+(?:without\s+me|if\s+i\s+(?:was|were|am)\s+(?:not\s+here|gone|dead))\b"
                 r"|\bbetter\s+when\s+(?:i|she|he)\s+(?:am|is)\s+gone\b"
                 r"|\b(?:fall|dose|amount)\b.{0,30}\b(?:fatal|lethal)\b"
                 # Indirect statements asked about "a person" or "someone", which the general
                 # question rule would otherwise let through. Each needs a sign of choosing to
                 # die or get hurt, because "What happens if we don't eat for a day?" and
                 # "What happens when a person stops breathing?" are lessons.
                 r"|\b(?:kill|hang|starve|poison)(?:s|es|ed|ing)?\s+(?:myself|yourself|himself|herself|themselves|oneself)\b"
                 r"|" + _NOT_FASTING + r"\b(?:on\s+purpose|deliberately|intentionally)\b.{0,50}" + _HARM_ACT
                 + r"|" + _NOT_FASTING + _HARM_ACT + r".{0,50}\b(?:on\s+purpose|deliberately|intentionally)\b"
                 r"|\b(?:stop\w*|not|n't|never)\s+(?:eating|drinking|eat|drink)\b.{0,40}"
                 r"\b(?:until|till|before)\s+(?:the\s+end|death|(?:they|he|she|i|you|a\s+person)\s+(?:die|dies|never\s+wakes?))\b"
                 r"|\bhold\w*\s+(?:their|his|her|my|your)\s+breath\b.{0,20}\b(?:until|till)\s+(?:they|he|she|i|you)\s+(?:die|dies)\b"
                 r"|\bdie\s+if\s+(?:they|he|she|i|you|we|a\s+person|someone)\s+stop\w*\s+(?:eating|drinking|taking)\b"
                 r"|\b(?:stop\w*|not)\s+(?:eating|eat)\b.{0,40}\b(?:disappear|fade\s+away|become\s+nothing)\b"
                 r"|\bwant\s+to\s+(?:fade\s+away|become\s+nothing|not\s+exist)\b"
                 r"|\bi\b.{0,20}\b(?:stop\w*|quit\w*)\s+(?:eating|drinking)(?:\s+and\s+(?:eating|drinking))?"
                 r"\s+(?:from\s+(?:today|now|tomorrow)|completely|altogether|forever|for\s+good|any\s*more)\b"
                 r"|\bnever\s+wake\s+up\b(?!\s+(?:early|on\s+time|in\s+time|before|at|for))"
                 r"|\b(?:painless|quick|quickest|easiest|fastest|simplest|surest)\s+(?:way|method)s?\b.{0,40}"
                 r"\b(?:to\s+die|dying|death|stop\s+living|end\s+(?:it|things))\b"
                 r"|\bpainless(?:ly)?\b.{0,20}\b(?:die|death|dying)\b|\bstop\s+living\s*(?:[?.!,]|$)"
                 r"|\b(?:die|death|dying)\s+by\s+(?:hanging|suicide|overdose|poisoning|jumping)\b"
                 r"|\b(?:drink|drinks|drank|drinking|swallow\w*|take|takes|took|eat|eats|ate)\s+(?:some\s+|the\s+|all\s+the\s+"
                 r"|a\s+(?:bottle|lot)\s+of\s+)?(?:pesticides?|insecticides?|phenyl|rat\s+poison|bleach|kerosene|toilet\s+cleaner)\b"
                 r"|\bswallow\w*\b.{0,25}\b(?:all|many|lots)\b.{0,15}\b(?:pills|tablets|medicines?)\b"
                 r"|\b(?:walk\w*|jump\w*|step\w*)\s+in\s+front\s+of\s+(?:a|the)\s+(?:moving\s+)?(?:train|bus|truck|lorry|car)\b"
                 r"|\b(?:lie|lies|lay|lays|lying)\s+(?:down\s+)?on\s+(?:a|the)\s+(?:railway\s+)?(?:track|tracks|line)\b"
                 r"|\b(?:a\s+person|someone|somebody|people|i|he|she|they|you|(?:a|the)\s+(?:boy|girl|man|woman|child|student))"
                 r"\s+(?:\w+\s+)?jump\w*\s+(?:in|into|down)\s+(?:a|the)\s+well\b(?!\s+to\s+(?:save|rescue|help|get|fetch))"
                 r"|\bcut\w*\s+(?:(?:my|your|his|her|their)\s+)?(?:own\s+)?wrists?\b|\bslit\w*\b.{0,20}\bwrists?\b",
    "abuse": r"\b(touch|touches|touched|touching)\b.*\b(me|her|him|my body|private|bad way)\b|\bbad touch\b"
             r"|\b(beat|beats|beating|hit|hits|hurt|hurts|slap|slaps|kick|kicks|burn|burns) (me|him|her)\b"
             r"|\bprivate (photos|pictures|parts)\b|\bforced? to marry\b|\block(s|ed)? (me|him|her)\b"
             r"|\bmolest|\brape[ds]?\b|\bharass"
             r"|\bi(?:'m| am)\b.*\b(scared|afraid|frightened|terrified)\b"
             # Fear of a person, secrets and unwanted contact, said without naming the harm.
             r"|\b(?:scared|afraid|frightened|terrified)\s+of\s+(?:my|her|his|their)\b"
             r"|\b(?:don'?t|do\s+not)\s+feel\s+safe\b|\b(?:comes?|came)\s+into\s+my\s+(?:room|bed)\b"
             r"|\b(?:told|tells|said|says)\s+me\s+not\s+to\s+tell\b|\bour\s+secret\b"
             r"|\bwhat\s+(?:he|she|they)\s+(?:does|did|do)\s+to\s+me\b|\bphotos?\s+i\s+did\s+not\s+ask\b",
    "violence": r"\b(make|making|build|building|get|buy|create)\b.*\b(bomb|explosives?|gun|pistol|weapon|poison)\b"
                r"|\b(hurt|kill|stab|beat up|attack|poison)\b (someone|somebody|a person|my|him|her|them|a classmate)\b"
                r"|\bat someone\b|\bsomeone's food\b|\bsteal\b|\bbreak into\b|\bshoplift"
                r"|\bset\b.*\bon fire\b|\bburn (down|(a|the) (house|building|school))\b",
    "adult": r"\bporn|\bsex\b(?! (ratio|determination|organs?|cells?|hormones?))|\bnaked\b|\bnude\b"
             r"|\bdirty (story|joke|picture|video)|\b(adult|blue) (video|film|movie|content)|\berotic"
             r"|\bsexy\b|\bmasturbat|\bsexual\s+(?:intercourse|acts?)\b|\bno\s+clothes\b"
             r"|\bcouples?\b.*\bin\s+bed\b|\bhot\s+(?:videos?|pictures?|photos?)\b",
    "hate": r"\b(caste|religion|community|race|tribe|tribal|muslims?|hindus?|christians?|sikhs?|dalits?|girls?|boys?|women|men)\b"
            r".*\b(worst|worse than|inferior|bad people|dumb|stupid|lowest|smarter than|should not (go|be educated)|should leave|bad at|should stay at home)\b"
            r"|\b(worst|lowest)\b.*\b(caste|religion)\b|\bless intelligent\b|\binsults?\b",
    # Opinions on social questions go to the teacher. Facts, like "What is the caste system?",
    # do not match, so they still get a textbook answer.
    "sensitive": r"\b(?:caste|cast)\b(?:\s+system)?\b.{0,20}\b(?:good|bad|right|wrong|fair|useful)\b"
                 r"|\buntouchability\b.{0,20}\b(?:good|right|okay|ok|fair)\b"
                 r"|\bwhich\s+(?:religion|caste)\s+is\s+(?:the\s+)?(?:best|better|right|true)\b"
                 r"|\bshould\s+girls\b.{0,30}\b(?:stop|not|leave)\b"
                 r"|\b(?:lower|upper)\s+castes?\b.{0,20}\b(?:meant|should|must|born)\b",
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
# A teacher asking for class material. The NLI reads "a lesson plan for weak students" as a
# person in danger, so these stop here too, under the same rule: nothing personal in the rest.
_TEACHING = re.compile(
    r"^(?:please\s+)?(?:(?:can|could|will|would)\s+you\s+)?(?:please\s+)?"
    r"(?:make|create|prepare|write|draft|plan|give\s+me|suggest)\s+(?:me\s+)?(?:a\s+|an\s+|some\s+|the\s+)?"
    r"(?:\w+\s+){0,2}(?:lesson\s+plans?|lessons?|quiz(?:zes)?|worksheets?|tests?|activit(?:y|ies)|questions)\b",
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
    "The speaker feels hopeless or worthless.",
    "The speaker wishes they did not exist.",
    "The speaker is afraid of someone who hurts them.",
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
_RESPONSE_FOR = {"self_harm": "self_harm", "abuse": "care", "concern": "unsure", "sensitive": "unsure", "medical": "medical",
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
        teaching = _TEACHING.match(request.strip())
        if teaching and not _PERSONAL.search(request.strip()[teaching.end():]):
            return None
        category = self._nli_category(request)
        return self._flag(category, "nli") if category else None

    def check_line(self, line: str) -> Optional[str]:
        """The category a teacher's everyday line is skipped for, or None if it may be used.

        Patterns first (check_teacher_line). Then the safety model reads the line as a
        statement (_LINE_CONCERNS against _LINE_SAFE), and the line is dropped if that reading
        flags it and either the line has a cue (_LINE_CUES) or the request check also flags it
        in the form an example takes ("For example, ..."). So the model needs a cue or a second
        reading of its own to agree. Not for requests: those go through check.
        """
        found = check_teacher_line(line)
        if found:
            return found

        def best(hypotheses):
            return max(self.verifier.entailment_margin(line, h) for h in hypotheses)
        margin = best(_LINE_CONCERNS + _CONCERNS) - best(_LINE_SAFE + _SAFE_LABELS)
        if margin <= LINE_NLI_MARGIN:
            return None
        if _LINE_CUES.search(line) or self.check("For example, " + line[:1].lower() + line[1:]):
            return "concern"
        return None

    @staticmethod
    def _flag(category: str, source: str) -> SafetyFlag:
        text = RESPONSES[_RESPONSE_FOR[category]]
        return SafetyFlag(category, source, text["en"], text["hi"])


# ------------------------------------------------------------ the teacher's own lines
# A teacher's everyday examples (see teacher_examples.py) get their own, looser check. The
# NLI above is tuned on a child's requests, and it flagged 9 of 33 harmless lines a teacher
# might write even in the "For example, ..." form (11 November), among them Holi, gilli danda,
# milking the buffalo and the weekly haat. The lines come from a trusted adult, so they are
# checked with patterns (the request patterns above, except the medical ones, and these) and
# then by the safety model, whose flag needs a cue or the request check to agree
# (SafetyCheck.check_line). Every example
# made from a line is still checked in full, by SafetyCheck.check, before a child hears it.
_LIVING = (r"(?:dogs?|pupp(?:y|ies)|cats?|kittens?|cows?|calf|calves|buffalo(?:es)?|goats?|kids?|sheep|lambs?"
           r"|bullocks?|ox|oxen|donkeys?|horses?|camels?|pigs?|hens?|chickens?|birds?|parrots?|monkeys?|animals?"
           r"|frogs?|snakes?|ants?|insects?|bees?|butterfl(?:y|ies)|lizards?|squirrels?|child|children|babies|baby|boys?|girls?|brothers?|sisters?|sons?|daughters?|wife|wives"
           r"|husbands?|mother|father|students?|friends?|classmates?|neighbours?|man|men|woman|women|people|person"
           r"|someone|somebody|him|her|them|me|us|myself|yourself|himself|herself|themselves)"
           # "Burning cow dung cakes" is about the dung, not the cow.
           r"(?!['\s]+(?:dung|cakes?|milk|ghee|sheds?|pens?|feed|fodder|carts?|bells?|horns?|food|toys?|kites?))")
_HUMAN = (r"(?:child|children|kids?|babies|baby|boys?|girls?|brothers?|sisters?|sons?|daughters?|wife|wives"
          r"|students?|friends?|classmates?|him|her|them|me|us)")
# The object right after a verb: "the stray dog", "his little sister", but not "the ball to the boy".
_OBJECT = (r"\s+(?:(?:the|a|an|his|her|their|my|your|our|its|that|this|those|these)\s+)?"
           r"(?:(?!(?:with|to|at|on|in|into|for|from|by|and|of|near|under)\b)[\w'-]+\s+){0,2}?")
_GROUP = r"(?:girls?|boys?|women|men|ladies|daughters?|sons?|wives|husbands?)"

_LINE_PATTERNS = {
    "self_harm": r"\b(?:die|dies|dying|died|suicid\w*|overdos\w*|noose)\b|\bdead\s+(?:body|bodies|person|man|woman|child|baby)\b"
                 r"|\bwant\w*\s+to\s+(?:die|disappear|not\s+live)\b|\bhang\w*\s+(?:from|on)\s+(?:the\s+)?(?:ceiling\s+)?fan\b"
                 r"|\brope\s+(?:a?round|on)\s+(?:the|his|her|their|your|my)?\s*neck\b"
                 r"|\b(?:cut|cuts|cutting|slash\w*|scratch\w*)\s+(?:your|my|his|her|their|the)?\s*(?:own\s+)?(?:arms?|wrists?|skin|legs?|thighs?)\b"
                 r"|\bsleeping\s+(?:tablets?|pills?)\b",
    "violence": r"\b(?:kill(?!\w*\s+time)\w*|murder\w*|stab\w*|strangl\w*|attack\w*|threaten\w*|kidnap\w*|behead\w*)"
                r"|\b(?:guns?|pistols?|revolvers?|rifles?|katta|bombs?|explosives?|grenades?|weapons?|swords?|daggers?)\b"
                r"|\bacid\s+(?:attack|on)\b|\bthrow\w*\s+acid\b|\bset\w*\b.{0,30}\bon\s+fire\b"
                r"|\bfight\w*\s+with\s+(?:knives|knife|sticks?|lathis?|stones?|swords?|blades?|sickles?|rods?)\b"
                r"|\b(?:knives|knife|blades?|sickles?|axes?|lathis?|sticks?|rods?)\b.{0,20}\b(?:at|after)" + r"\s+(?:the\s+|a\s+|his\s+|her\s+|their\s+)?" + _LIVING + r"\b"
                # Animals take food in plenty of everyday lines; people stealing is left out.
                r"|^(?!.*\b(?:monkeys?|crows?|birds?|cats?|dogs?|rats?|mice|squirrels?|parrots?|goats?|cows?)\b).*\b(?:steal\w*|stole\w*|shoplift\w*|pickpocket\w*)",
    "cruelty": r"\b(?:beat|beats|beating|beaten|hit|hits|hitting|kick|kicks|kicking|kicked|slap\w*|punch\w*|whip\w*|thrash\w*"
               r"|tortur\w*|smack\w*|spank\w*|burn|burns|burning|burnt|burned|pelt\w*|scald\w*|starv\w*|hurt\w*|injur\w*"
               r"|harm\w*|poison\w*|abus\w*|hang|hangs|hanging|hanged|squeez\w*|drown\w*)" + _OBJECT + _LIVING + r"\b"
               r"|\b(?:hot|burning|boiling)\s+(?:[\w'-]+\s+){0,2}(?:to|on|at|over)\s+(?:the\s+|a\s+|an\s+)?" + _LIVING + r"\b"
               r"|\b(?:lock\w*|tie|ties|tying|tied|chain\w*|forc\w*|grab\w*|drag\w*)" + _OBJECT + _HUMAN + r"\b"
               r"|\bthrow\w*\s+(?:stones?|rocks?|bricks?|pebbles?)\s+at\b|\bpull\w*\s+(?:\S+\s+){0,2}(?:tails?|ears?|hair)\b"
               r"|\b(?:pinch\w*|twist\w*)\b.{0,25}\b(?:ears?|arms?|hair|cheeks?|noses?|skin)\b"
               r"|\b(?:on\s+purpose|as\s+(?:a\s+)?punishment|as\s+a\s+(?:joke|prank)|for\s+a\s+(?:joke|prank|laugh)|to\s+(?:scare|frighten|hurt|tease))\b"
               r"|\bfeed\w*\b.{0,20}\bplastic\b|\bspit\w*\s+(?:on|at)\b|\bmock\w*|\bmak\w*\s+fun\s+of\b|\blaugh\w*\s+at\b"
               r"|\b(?:teas(?:e|es|ed|ing)|bull(?:y|ies|ied|ying)|ragging|eve[\s-]?teas\w*|punish\w*)\b"
               r"|\bmak\w*\s+(?:the\s+|a\s+)?(?:child|children|kids?|boys?|girls?)\s+(?:to\s+)?(?:work|carry|labou?r)\b|\bchild\s+labou?r\b",
    "abuse": r"\btouch\w*\b.{0,40}\b(?:girls?|boys?|child|children|kids?|private|body|bodies|nobody|alone|secret)\b"
             r"|\bsecrets?\b(?!\s+(?:recipe|ingredient|hiding\s+place|garden))|\bbruis\w*"
             r"|\b(?:nobody|no\s+one)\s+(?:else\s+)?(?:is\s+)?(?:at\s+)?home\b"
             r"|\bstrangers?\b.{0,40}\b(?:cars?|bikes?|sweets|chocolates?|toffees?|took|take|takes|taking|follow\w*|gave|give|gives"
             r"|giving|offer\w*|call\w*|touch\w*|alone|home|room)\b"
             r"|\balone\b.{0,30}\b(?:night|field|forest|house|room|man|uncle|stranger|pond|well|canal|river)\b"
             r"|\b(?:girls?|child|children|baby|kids?|boys?)\b.{0,30}\balone\b"
             r"|\boffer\w*\s+(?:the\s+|a\s+)?(?:children|child|kids?|girls?|boys?)\s+(?:money|sweets|toffees?|chocolates?|gifts?|rides?)\b"
             r"|\b(?:money|sweets|toffees?|chocolates?|gifts?)\b.{0,20}\bto\s+come\b"
             r"|\b(?:child|early)\s+marriage\b|\bmarried\s+(?:at|off|young)\b|\bmarry\s+(?:off|her|him)\b|\bdowry\b",
    "adult": r"\bkiss\w*|\bnaked\b|\bnude\w*|\bundress\w*|\bporn\w*|\bsexy?\b|\bsexual\w*|\bbreasts?\b|\bpeep(?:s|ed|ing)?\b"
             r"|\bdirty\s+(?:videos?|pictures?|photos?|films?|movies?|talk|books?|jokes?|stor(?:y|ies))\b"
             r"|\bblue\s+(?:films?|movies?)\b|\bprivate\s+(?:parts?|photos?|pictures?|videos?)\b"
             r"|\b(?:take|takes|taking|took|pull\w*)\s+off\s+(?:his|her|their|your|my|the)\s+(?:shorts|pants|clothes|frock|shirt"
             r"|dress|underwear|kurta|salwar|chaddi)\b",
    "drugs": r"\b(?:daru|sharab|alcohol\w*|liquor|beer|wine|whisk(?:e)?y|rum|toddy|tadi|hooch|arrack|feni|bhang|ganja|charas"
             r"|afeem|opium|heroin|cocaine|drugs?|bidis?|beedis?|cigarettes?|cigars?|hookah|chillum|gutk?ha|gutka|tobacco|khaini"
             r"|zarda|drunk\w*|intoxicat\w*|sniff\w*|glue|whitener)\b|\bwhite\s+powder\b"
             # "The smoking chulha" and "smoking out the bees" are everyday.
             r"|(?<!the )(?<!a )\bsmok(?:ing|es|ed|er|ers)\b(?!\s+(?:out|fish|meat|chill?ies|bees|the\s+bees|neem|mosquito\w*))"
             r"|\bdrink\w*\s+with\b|\bafter\s+drinking\b",
    "danger": r"\bplay\w*\s+with\s+(?:a\s+|the\s+|some\s+)?(?:fire|matches|matchsticks?|match\s*box|lighters?|petrol|kerosene"
              r"|diesel|knives|knife|blades?|razors?|(?:fire)?crackers?|electric\w*|wires?|plugs?|sockets?|acid|medicines?"
              r"|tablets?|pills?|poison\w*)\b"
              r"|\bplay\w*\s+(?:on|near|beside|along|across)\s+(?:the\s+)?(?:road|highway|railway|tracks?|rail\s+line|transformer)\b"
              r"|\b(?:petrol|kerosene|diesel)\b.{0,40}\bfire\b|\bfire\b.{0,40}\b(?:petrol|kerosene|diesel)\b"
              r"|\b(?:fire|chulha|stove|angithi|coal|charcoal|heater)\b.{0,30}\b(?:closed|shut)\s+(?:room|doors?|windows?)\b"
              r"|\b(?:breath\w*|inhal\w*)\s+(?:in\s+)?(?:the\s+)?(?:petrol|kerosene|glue|whitener|fumes|gas)\b"
              r"|\bpour\w*\s+(?:hot|boiling)\s+(?:water|oil)\s+(?:on|over)\b"
              r"|\b(?:pesticides?|insecticides?|poison\w*|phenyl|bleach)\b.{0,30}\b(?:into|in)\s+(?:the\s+|a\s+|his\s+|her\s+|their\s+)?"
              r"(?:[\w]+'s\s+)?(?:food|water|tanks?|wells?|sweets?|drinks?|milk|tea|dal|rice|pots?)\b"
              # Vehicles
              r"|\b(?:roofs?|top)\s+of\s+(?:the\s+|a\s+)?(?:moving\s+)?(?:tractors?|bus|buses|trains?|trucks?|lorry|lorries|trolleys?|jeeps?)\b"
              r"|\b(?:tractor|bus|train|truck|lorry|trolley|jeep)\s+roofs?\b"
              r"|\b(?:hang\w*|cling\w*|jump\w*|stand\w*|rid(?:e|es|ing))\b.{0,30}\b(?:moving|running)\s+(?:bus|train|tractor|truck|lorry|trolley|jeep)\b"
              r"|\b(?:onto|on|off|from|into)\s+(?:the\s+|a\s+)?moving\b"
              r"|\b(?:cycl\w*|play\w*|run\w*|cross\w*|walk\w*|rac\w*)\b.{0,30}\b(?:highway|traffic)\b|\bagainst\s+(?:the\s+)?traffic\b"
              r"|\b(?:rac\w*|run\w*|cycl\w*|cross\w*)\s+(?:across|over|along|on)\s+(?:the\s+)?(?:railway\s+)?(?:tracks?|rail\s+lines?|railway\s+lines?|railway)\b"
              r"|\bgas\b.{0,30}\bcylinder\b|\bcylinder\b.{0,30}\b(?:gas|leak\w*)\b|\bleak\w*\b.{0,20}\bgas\b"
              r"|\bwet\s+(?:heater|plug|socket|switch|wire|iron)s?\b|\b(?:heater|socket|plug|switch)\b.{0,30}\bbath\w*"
              r"|\b(?:edge|rim)\s+of\s+(?:the\s+|an\s+)?(?:open\s+)?(?:well|roof|terrace|cliff|quarry|pit)\b|\bopen\s+(?:well|borewell|drain|manhole)s?\b"
              r"|\b(?:hang\w*|cling\w*|stand\w*)\s+(?:on|from|at)\s+(?:the\s+)?(?:back|door|footboard|outside|steps?)\s+of\b"
              r"|\b(?:hang\w*|lean\w*)\s+out\s+of\b|\bwithout\s+(?:a\s+)?helmets?\b|\btriple\s+riding\b|\briding\s+three\b"
              r"|\b(?:child|children|kids?|boys?|girls?)\s+driv\w*"
              r"|\b(?:lie|lies|lying|lay|lays|sit\w*|walk\w*|play\w*|stand\w*|sleep\w*)\s+(?:down\s+)?(?:on|along|across)\s+(?:the\s+)?(?:railway\s+)?(?:tracks?|rail\s+lines?|railway\s+lines?)\b"
              # Electricity
              r"|\b(?:electric|live|bare|power|high\s+tension)\s+(?:wires?|poles?|lines?|cables?)\b|\btransformers?\b"
              r"|\bwet\s+hands?\b.{0,30}\b(?:switch\w*|plugs?|sockets?|wires?)\b|\b(?:switch\w*|plugs?|sockets?|wires?)\b.{0,30}\bwet\s+hands?\b"
              r"|\b(?:pins?|hairpins?|wires?|nails?|fingers?|keys?|spoons?)\b.{0,25}\b(?:plug\s+points?|sockets?|switch\s*boards?)\b"
              # Heights and holes
              r"|\bclimb\w*\s+(?:up\s+)?(?:on\s+)?(?:the\s+|an\s+|a\s+)?(?:electric\s+|power\s+|telephone\s+|mobile\s+)?(?:poles?|towers?)\b"
              r"|\bclimb\w*\b.{0,30}\b(?:water\s+tanks?|tanks?\s+ladder|chimney|scaffold\w*)\b"
              r"|\b(?:climb\w*|fall\w*|jump\w*|crawl\w*)\s+(?:into|down)\b.{0,25}\b(?:silos?|tanks?|wells?|pits?|bore\s*wells?|drains?|sewers?)\b"
              r"|\bjump\w*\s+(?:off|from|down\s+from)\s+(?:the\s+|a\s+)?(?:roof|terrace|wall|tree|bridge|train|bus|building|balcony)\b"
              r"|\bjump\w*\s+(?:across|between|over)\s+(?:the\s+)?(?:roofs?|terraces?|walls?|wells?)\b"
              # Things that are not food
              r"|\b(?:eat\w*|ate|swallow\w*|chew\w*|lick\w*|tast(?:e|es|ed|ing)|drink\w*|drank|sip\w*)\s+(?:the\s+|some\s+|a\s+)?(?:[\w'-]+\s+){0,2}?"
              r"(?:mud|soil|clay|chalk|ash|ashes|coal|soap|dhatura|datura|poison\w*|pesticides?|insecticides?|phenyl|kerosene"
              r"|petrol|diesel|bleach|acid|urea|fertili[sz]ers?|batter(?:y|ies)|coins?|buttons?|marbles?|tablets?|pills?"
              r"|medicines?|mushrooms?|berries)\b"
              r"|\b(?:coins?|buttons?|batter(?:y|ies)|marbles?|beads?|pins?|seeds?|stones?|pebbles?)\b.{0,25}\b(?:mouths?|noses?|ears?)\b"
              # Fire and water
              r"|\b(?:(?:fire)?crackers?|(?:bottle\s+)?rockets?)\b.{0,25}\b(?:hands?|pockets?|dogs?|cats?|animals?|tails?|faces?|bottles?|inside)\b"
              r"|\b(?:hands?|pockets?|tails?)\b.{0,25}\b(?:fire)?crackers?\b|\bburn\w*\s+(?:the\s+)?(?:plastic|tyres?|tires?|rubber|polythene)\b"
              r"|\b(?:swim\w*|bath\w*|jump\w*|div(?:e|es|ed|ing))\b.{0,40}\b(?:alone|floods?|flooded|deep\s+water|whirlpool|dams?)\b"
              r"|\bjump\w*\s+(?:in|into|down)\s+(?:the\s+|a\s+)?(?:well|river|canal|dam)\b"
              r"|\b(?:climb\w*|swim\w*|bath\w*|wander\w*)\b.{0,40}\balone\b",
    "hate": r"\b(?:castes?|jati|jaati|untouchab\w*|achhut|dalits?|harijans?|savarna|chamars?|bhangis?|chuhras?|katuas?|kafirs?"
            r"|mlecchas?|neech|hindus?|muslims?|musalmans?|christians?|sikhs?|jains?|buddhists?|parsis?)\b"
            r"|\b(?:lower|upper|low|high|higher)\s+(?:jat|jaat|caste|castes|class|community|communities)\b"
            r"|\b" + _GROUP + r"\s+(?:are|is)\s+(?:not|never|always|only|less|more|better|worse|weaker|stronger|smarter"
            r"|cleverer|cleaner|dirtier|lazy|lazier|stupid|dumb|meant|born|too)\b"
            r"|\b" + _GROUP + r"\s+(?:should|must|cannot|can't|shouldn't|don't|do\s+not|never|ought)\b"
            r"|\b(?:dirty|filthy|lazy|stupid|dumb|useless|ugly|dangerous)\s+(?:people|caste|community|tribe|race|folk)\b"
            r"|\b(?:people|those|they|men|women|communit(?:y|ies)|labourers?|workers?|servants?|maids?|beggars?|villagers|the\s+poor)\b"
            r".{0,30}\b(?:are|is)\s+(?:all\s+)?(?:\w+\s+and\s+)?(?:thieves|dirty|filthy|lazy|liars?|cheats?|criminals?|stupid|dumb|useless)\b"
            r"|\bcall\w*\s+(?:\S+\s+){0,4}?(?:ugly|fat|dirty|stupid|dumb|idiot|useless|names?)\b|\b(?:dark|black)[\s-]+skinned\b",
}
_LINE_COMPILED = {name: re.compile(p, re.IGNORECASE) for name, p in _LINE_PATTERNS.items()}
# The request patterns that also hold for a statement. The medical ones are about asking for
# treatment, and a line about a home remedy is left to the full check on the finished example.
_REQUEST_PATTERNS_FOR_LINES = ("self_harm", "abuse", "violence", "adult", "hate", "sensitive")


def check_teacher_line(line: str) -> Optional[str]:
    """The patterns part of SafetyCheck.check_line: the category a teacher's line is skipped
    for, or None. For the teacher's own file only."""
    for category in _REQUEST_PATTERNS_FOR_LINES:
        if _COMPILED[category].search(line):
            return category
    for category, pattern in _LINE_COMPILED.items():
        if pattern.search(line):
            return category
    return None


# The safety model reads a teacher's line as a statement, against these. On its own it flags
# everyday lines at random: a swing at Teej "is sexual", picking lice "says one group is
# worse". So its flag counts only with a cue word (_LINE_CUES) or when the request check
# also flags the line in its "For example, ..." form. On 288 everyday lines and 185 harmful
# ones (11 November), this kept 269 and blocked 175, where the request check alone kept 170
# and blocked 164. Most harmful lines were written while the patterns were being made. On
# the last 25, written after, it blocked 18 and the request check 20, so a teacher's list
# should still be read before it is installed, and every example is checked in full.
# Without the second reading it kept 280 and blocked 171, and 14 of the last 25.
LINE_NLI_MARGIN = 1.0
_LINE_CONCERNS = [
    "Someone is being hurt.", "A child is in danger.", "This is dangerous for a child.",
    "Someone is being hit or beaten.", "An animal is being hurt.", "This is about alcohol, tobacco or drugs.",
    "This is sexual.", "This insults a group of people.", "Someone wants to die.", "This is about a weapon.",
    "A child is being abused.", "Someone could get badly injured.",
]
_LINE_SAFE = [
    "This is an everyday chore.", "This is a festival or celebration.", "This is a children's game.",
    "This is farm work.", "This is about cooking or food.", "This is about nature or animals.",
    "This is a normal part of village life.",
]
# People, bodies, force, and things that can hurt. Broad on purpose: a cue only lets the model
# decide, and most everyday lines have none.
_LINE_CUES = re.compile(
    r"\b(?:him|them|himself|herself|themselves|yourself|myself|uncle|strangers?|teenagers?|gangs?|classmates?|crowd"
    r"|heads?|necks?|eyes?|faces?|bod(?:y|ies)|bath(?:e|es|ing)|photos?|videos?|phones?|naked|alone|secrets?"
    r"|push\w*|throw\w*|drop\w*|lock\w*|break\w*|burn\w*|forc\w*|cut\w*|hit\w*|beat\w*|kick\w*|hurt\w*|slap\w*|chok\w*"
    r"|drown\w*|tie|tied|tying|grab\w*|drag\w*|scar(?:e|ed|ing)|sell\w*|sold|dar(?:e|es|ed|ing)|punish\w*"
    r"|mak(?:e|es|ing)\s+(?:the\s+|a\s+|his\s+|her\s+)?(?:\w+\s+)?\w+\s+(?:eat|drink|dance|stand|kneel|do|take|touch|undress|work)"
    r"|poison\w*|petrol|kerosene|diesel|razors?|blades?|knife|knives|sticks?|stones?|bottles?|fire\w*|crackers?|plastic\s+bags?"
    r"|iron|acid|wires?|wells?|bridges?|roofs?|stairs|terrace|motorcycles?|motorbikes?|bikes?|rac(?:e|es|ing)|speed\w*"
    r"|under\s*water|pills?|tablets?|medicines?|smok\w*|drink\w*|drunk\w*|blood|bleed\w*|wounds?|bruis\w*|pain\w*|cry\w*|tears?"
    r"|ropes?|hang\w*|pour\w*|squeez\w*|boiling|pesticides?|insecticides?|phenyl|bleach|heavy|moving|trains?|inside|closed)\b",
    re.IGNORECASE,
)
