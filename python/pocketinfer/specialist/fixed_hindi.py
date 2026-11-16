"""Hand-written Hindi for the device's own fixed sentences.

The translator handles answers well but mangles some short fixed lines when they arrive on
their own: "Noted." came back as "उल्लेखनीय है" ("remarkable"), and "Not quite." as
"बिल्कुल नहीं" ("absolutely not"), which is harsh for a student who got a quiz answer
wrong (Hindi journeys on the Jetson, 15 Nov 2026). The voice loop's command replies already
carry their own Hindi (session.CLOSE_REPLY and the rest). This does the same for the quiz,
the lesson plan and the revision.

A template's {topic} or {covered} is still translated, on its own, and {grade} is kept as
it is. A sentence not in the table is translated as before.
"""

from __future__ import annotations

import re
from typing import Callable, List, Optional, Tuple

from . import lesson_plan, quiz, revision

_PAIRS = [
    # Quiz
    (quiz.WHICH_TOPIC, "क्विज़ किस विषय पर हो?"),
    (quiz.NO_QUESTIONS, "मुझे {topic} पर पाठ्यपुस्तक का कोई सवाल नहीं मिला, इसलिए क्विज़ नहीं हो सकती।"),
    (quiz.INTRO, "यह {topic} पर क्विज़ है, सवाल पाठ्यपुस्तक से हैं। बीच में ख़त्म करना हो तो बस कहिए।"),
    (quiz.FIRST, "पहला सवाल।"),
    (quiz.NEXT, "अगला सवाल।"),
    (quiz.RIGHT, "यह सही है।"),
    (quiz.NOT_QUITE, "पूरी तरह सही नहीं। पाठ्यपुस्तक में यह लिखा है।"),
    (quiz.CANT_CHECK, "मैं इस जवाब को पाठ्यपुस्तक से जाँच नहीं पा रहा, तो आगे बढ़ते हैं।"),
    (quiz.LAST, "{topic} पर यह आख़िरी सवाल था। आप सबने बहुत अच्छा किया।"),
    (quiz.STOPPED, "ठीक है, क्विज़ ख़त्म।"),
    # Lesson plan
    (lesson_plan.INTRO["all"], "यह {topic} पर पाठ योजना है, अलग-अलग स्तर के बच्चों वाली कक्षा के लिए।"),
    (lesson_plan.INTRO["support"], "यह {topic} पर पाठ योजना है, उन बच्चों के लिए जिन्हें ज़्यादा मदद चाहिए।"),
    (lesson_plan.INTRO["stretch"], "यह {topic} पर पाठ योजना है, उन बच्चों के लिए जो आगे हैं।"),
    (lesson_plan.RECAP_COVERED, "शुरुआत में {covered} को थोड़ा दोहराइए। कक्षा से पूछिए कि उन्हें क्या याद है।"),
    (lesson_plan.RECAP_FRESH, "शुरुआत में कक्षा से पूछिए कि वे {topic} के बारे में पहले से क्या जानते हैं।"),
    (lesson_plan.KEY_IDEAS, "फिर पाठ्यपुस्तक के ये मुख्य बिंदु पढ़ाइए।"),
    (lesson_plan.KEY_IDEAS_ALL, "फिर सभी को पाठ्यपुस्तक के ये मुख्य बिंदु पढ़ाइए।"),
    (lesson_plan.SIMPLE_ALL, "जिन बच्चों को ज़्यादा मदद चाहिए, उन्हें इसे और आसान तरीके से समझाइए।"),
    (lesson_plan.SIMPLE_ONLY, "फिर ये बातें एक-एक करके आसान तरीके से समझाइए।"),
    (lesson_plan.CHECK_QUOTED, "सबको समझ आया या नहीं, यह जाँचने के लिए पाठ्यपुस्तक का यह सवाल पूछिए।"),
    (lesson_plan.CHECK_QUOTED_MANY, "इसके बाद कक्षा से पाठ्यपुस्तक के ये सवाल पूछिए।"),
    (lesson_plan.ASK_FIXED, "इसके बाद दो-तीन बच्चों से {topic} अपने शब्दों में समझाने को कहिए।"),
    (lesson_plan.STRETCH_QUOTED, "जो बच्चे आगे हैं, उनके लिए पाठ्यपुस्तक का यह सवाल पूछिए।"),
    (lesson_plan.STRETCH_QUOTED_MANY, "इसके बाद पाठ्यपुस्तक के ये सवाल पूछिए, जिनमें ज़्यादा सोचना पड़ता है।"),
    (lesson_plan.STRETCH_FIXED, "जो बच्चे आगे हैं, उनसे कहिए कि वे {topic} उस साथी को समझाएँ जिसे मदद चाहिए।"),
    (lesson_plan.CLOSE, "आख़िर में एक बच्चे से {topic} को एक वाक्य में बताने को कहिए।"),
    (lesson_plan.WHICH_TOPIC, "पाठ किस विषय पर है?"),
    (lesson_plan.NOTED, "ठीक है, नोट कर लिया। जब आप तैयार हों, अगली कक्षा के लिए पाठ योजना माँग लीजिए।"),
    # Revision
    (revision.INTRO, "यह {topic} का छोटा-सा रिवीजन है।"),
    (revision.INTRO_GRADE, "यह कक्षा {grade} के लिए {topic} का छोटा-सा रिवीजन है।"),
    (revision.ASK_ONE, "अब कक्षा से पाठ्यपुस्तक का यह सवाल पूछिए।"),
    (revision.ASK_MANY, "अब कक्षा से पाठ्यपुस्तक के ये सवाल पूछिए।"),
    (revision.ASK_FIXED, "अब दो-तीन बच्चों से {topic} अपने शब्दों में समझाने को कहिए।"),
    (revision.CLOSE, "{topic} का रिवीजन यहीं पूरा हुआ। कक्षा से पूछिए कि क्या कुछ अब भी साफ़ नहीं है।"),
    (revision.WHICH_TOPIC, "किस विषय का रिवीजन कराऊँ? जैसे कहिए, कक्षा 7 के साथ प्रकाश संश्लेषण का रिवीजन कराइए।"),
    (revision.OTHER_GRADE, "मैं सिर्फ़ कक्षा 6, 7 और 8 के विषयों का रिवीजन करा सकता हूँ।"),
]

_FIELD = re.compile(r"\{(\w+)\}")
_TRANSLATED = {"topic", "covered"}   # English words from the request, translated on their own


def _pattern(template: str) -> "re.Pattern":
    parts, last = [], 0
    for m in _FIELD.finditer(template):
        parts.append(re.escape(template[last:m.start()]))
        parts.append(rf"(?P<{m.group(1)}>.+?)")
        last = m.end()
    parts.append(re.escape(template[last:]))
    return re.compile("^" + "".join(parts) + "$", re.DOTALL)


# Most fixed words first, so "revision of {topic} for class {grade}." is tried before
# "revision of {topic}." would swallow "photosynthesis for class 7" as the topic.
_TABLE: List[Tuple["re.Pattern", str]] = [
    (_pattern(en), hi) for en, hi in sorted(_PAIRS, key=lambda p: -len(_FIELD.sub("", p[0])))
]


def fixed_hindi(text_en: str, translate: Callable[[str], str]) -> Optional[str]:
    """The hand-written Hindi for one of the device's fixed sentences, or None.

    translate turns an English phrase into Hindi; it is used only for {topic} and {covered}.
    """
    text = (text_en or "").strip()
    for pattern, hindi in _TABLE:
        m = pattern.match(text)
        if not m:
            continue
        values = {}
        for name, value in m.groupdict().items():
            if name in _TRANSLATED:
                value = (translate(value) or value).strip().strip("।.?!").strip()
            values[name] = value
        return hindi.format(**values)
    return None
