"""Hand-written Hindi for the device's fixed sentences, in place of a translation."""

import re

import pytest

from pocketinfer.specialist import lesson_plan, quiz, revision
from pocketinfer.specialist.fixed_hindi import _PAIRS, fixed_hindi
from pocketinfer.specialist.pipeline import SpecialistEngine

TOPIC_HI = {"photosynthesis": "प्रकाश संश्लेषण।", "nutrition in plants": "पौधों में पोषण"}


def translate(phrase):
    return TOPIC_HI.get(phrase, "<" + phrase + ">")


@pytest.mark.parametrize("english, hindi", _PAIRS)
def test_every_fixed_sentence_gets_its_own_line(english, hindi):
    said = english.format(topic="photosynthesis", covered="photosynthesis", grade=7)
    want = hindi.format(topic="प्रकाश संश्लेषण", covered="प्रकाश संश्लेषण", grade=7)
    assert fixed_hindi(said, translate) == want


@pytest.mark.parametrize("english, hindi", _PAIRS)
def test_fields_match(english, hindi):
    assert sorted(re.findall(r"\{(\w+)\}", english)) == sorted(re.findall(r"\{(\w+)\}", hindi))
    assert "—" not in hindi


def test_the_lines_the_translator_got_wrong():
    assert fixed_hindi(lesson_plan.NOTED, translate).startswith("ठीक है, नोट कर लिया।")
    assert "बिल्कुल नहीं" not in fixed_hindi(quiz.NOT_QUITE, translate)


def test_grade_template_wins_over_the_plain_one():
    said = revision.INTRO_GRADE.format(topic="nutrition in plants", grade=8)
    assert fixed_hindi(said, translate) == "यह कक्षा 8 के लिए पौधों में पोषण का छोटा-सा रिवीजन है।"


def test_an_answer_sentence_is_left_to_the_translator():
    assert fixed_hindi("Plants make their own food.", translate) is None


class _Engine:
    """Only what SpecialistEngine.say needs."""

    def __init__(self):
        self.translated = []

    def _localize_text(self, text, lang):
        self.translated.append(text)
        return "MT:" + text if lang != "en" else text

    def _synthesize(self, text, lang):
        return None


def test_say_uses_the_hindi_line_and_translates_only_the_topic():
    e = _Engine()
    r = SpecialistEngine.say(e, quiz.LAST.format(topic="photosynthesis"), "hi")
    assert r.text_en == quiz.LAST.format(topic="photosynthesis")
    assert r.spoken_text == "MT:photosynthesis पर यह आख़िरी सवाल था। आप सबने बहुत अच्छा किया।"
    assert e.translated == ["photosynthesis"]


def test_say_in_english_and_unknown_sentences_are_unchanged():
    e = _Engine()
    assert SpecialistEngine.say(e, lesson_plan.NOTED, "en").spoken_text == lesson_plan.NOTED
    assert SpecialistEngine.say(e, "Something new.", "hi").spoken_text == "MT:Something new."
