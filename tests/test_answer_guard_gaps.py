"""Known answer gaps: a name paired with an invention it did not make, how-to requests for
everyday tasks that share one word with a lesson, lesson aims read out as answers, and a
misheard topic word answered from the request's other words."""

from types import SimpleNamespace

import pytest

from pocketinfer.specialist.answer_guard import contradicts, lesson_aim, unsupported_credit
from pocketinfer.specialist.grounding import (
    REFUSAL_TEXT, asks_how_to, build_grounding, closest_term, index_words, topic_phrase, unknown_words,
)
from pocketinfer.specialist.pipeline import SpecialistEngine
from pocketinfer.specialist.vector_index import Retrieved

# Page 94 of the Class 7 science book, as the index holds it.
EDISON_BOX = (
    "Did you know? The credit for the invention of the electric bulb is usually given to Thomas Alva "
    "Edison, though others before him had worked on it. Edison was a remarkable man. He made some 1300 "
    "inventions including the electric bulb, gramophone, the motion picture camera and the carbon "
    "transmitter, which facilitated the invention of the telephone. Fig. "
)
EDISON_LABELS = (
    "bulb, gramophone, the motion picture camera and the carbon transmitter, which facilitated the "
    "invention of the telephone. Fig. 10.27 Fig. 10.27 Fig. 10.27 Thomas Alva Edison (A.D. 1847 – 1931) "
    "Fig. 10.26 A working model of a railway signal 4."
)
HARVEY = ("From the kidneys, the urine goes into the urinary bladder The English physician, William Harvey "
          "(A.D.1578–1657), discovered the circulation of blood.")


@pytest.mark.parametrize("sentence, passage", [
    ("Thomas Alva Edison invented the telephone.", EDISON_BOX),
    ("Thomas Alva Edison invented the telephone.", EDISON_LABELS),
    ("The telephone was invented by Edison.", EDISON_BOX),
    ("Edison is credited with the invention of the telephone.", EDISON_BOX),
    ("Harvey discovered the working of the stomach.",
     "William Harvey discovered the circulation of blood. The working of the stomach was discovered by a soldier."),
    ("Galileo discovered the pendulum.", "Galileo did not discover the pendulum. Galileo studied it."),
])
def test_credit_the_passage_never_gives_is_caught(sentence, passage):
    assert unsupported_credit(sentence, passage)
    assert contradicts(sentence, [passage])


@pytest.mark.parametrize("sentence, passage", [
    ("Edison invented the electric bulb.", EDISON_BOX),
    ("Edison invented the electric light bulb.", EDISON_BOX),
    ("Thomas Alva Edison is given credit for inventing the electric bulb.", EDISON_BOX),
    ("Edison invented the gramophone and the motion picture camera.", EDISON_BOX),
    ("Edison made some 1300 inventions.", EDISON_BOX),
    ("The carbon transmitter facilitated the invention of the telephone.", EDISON_BOX),
    ("Alexander Parkes invented plastic.", "1 Alexander Parkes invented plastic."),
    ("Plastic was invented by Alexander Parkes.", "1 Alexander Parkes invented plastic."),
    ("William Harvey discovered the circulation of blood.", HARVEY),
    ("Dorothy Hodgkin discovered the structure of vitamin B12.",
     "Know a Scientist Dorothy Hodgkin (3rd women noble laureate) was a brilliant scientist discovered the 3D "
     "structure of vitamin B12 in 1956,and won the Nobel"),
    ("Scientists discovered that cells divide.", "Scientists discovered that cells divide."),
    ("Paheli discovered a layer of humus.",
     "Soon, Paheli removed some dead leaves and discovered under them a layer of humus on forest floor."),
])
def test_credit_the_passage_gives_is_kept(sentence, passage):
    assert not unsupported_credit(sentence, passage)


def test_credit_counts_when_any_passage_gives_it():
    assert not contradicts("Edison invented the electric bulb.", [EDISON_LABELS, EDISON_BOX])


def _passage(text, chapter="c"):
    return Retrieved(0.9, text, "do_1", chapter, "page 1", 1, "")


# Two of the passages search returned for "How do I cook food", shortened.
COOKING = [
    _passage("Have you noticed cross-sections of some vegetables when they are cut for the purposes of "
             "cooking in the kitchen?", "Visualising Solid Shapes"),
    _passage("Recalls the food components and completes the list. Concepts Covered: Different methods of "
             "taking food", "Nutrition in Animals"),
]


def test_how_to_for_a_task_no_passage_names_is_refused():
    g = build_grounding("How do I cook food", COOKING, min_score=0.3)
    assert g.refused and "task" in g.reason


@pytest.mark.parametrize("request_", [
    "How do I add two fractions?", "How can we prevent rusting?", "How to find the area of a rectangle?",
])
def test_how_to_for_a_task_a_passage_teaches_is_answered(request_):
    lesson = [_passage("To add two fractions, find a common denominator. To find the area of a rectangle, "
                       "multiply length by breadth. We can prevent rusting by painting iron.")]
    assert not build_grounding(request_, lesson, min_score=0.3).refused


def test_how_something_works_is_not_held_to_the_task_rule():
    assert not asks_how_to("How do plants make food?")
    assert not asks_how_to("How does the heart pump blood?")
    assert not asks_how_to("How do you add two fractions?")
    assert asks_how_to("How do I cook food")
    assert asks_how_to("how can we make an electromagnet")
    plants = [_passage("Plants make their food by photosynthesis, using sunlight.")]
    assert not build_grounding("How do plants make food?", plants, min_score=0.3).refused


# ---------------------------------------------------------------- lesson aims, 12 November
# "tell me about the heat endaction" was answered with these two sentences, rephrased from lesson plans.
HEAT_PLAN = ("Objective Given the content (chapter) the learners will be able to explain the different modes "
             "of heat transfer. Students will be able understand wool keeps us warm in winters.")


@pytest.mark.parametrize("sentence", [
    "The teacher will guide students through an experiment to observe heat transfer by conduction, classify "
    "objects as good and bad conductors, and explain heat transfer by convection and radiation.",
    "Students will also justify why they wear certain kinds of clothes in summer and winter.",
    "Learners will be able to explain the different modes of heat transfer.",
    "By the end of this lesson, students can name three modes of heat transfer.",
    "The learning outcomes of this chapter are to classify good and bad conductors.",
    "Children will learn that wool is a poor conductor of heat.",
    "The aim of this activity is to show that metals conduct heat.",
    "Objective: To observe heat transfer by conduction.",
])
def test_a_lesson_aim_is_caught(sentence):
    assert lesson_aim(sentence)
    assert contradicts(sentence, [HEAT_PLAN])


@pytest.mark.parametrize("sentence", [
    "Heat flows from a hotter object to a colder object.",
    "Woollen clothes keep us warm in winter because wool traps air, which is a poor conductor of heat.",
    "Students who touch a hot pan feel the heat move into their hand.",
    "Children who drink dirty water will fall ill.",
    "The objective lens of a microscope is close to the object.",
    "Ask your teacher to help you heat the water.",
])
def test_a_fact_is_not_taken_for_a_lesson_aim(sentence):
    assert not lesson_aim(sentence)


# ---------------------------------------------------------- a topic word the index never uses
def _chunk(text):
    return {"text": text, "content_id": "b", "chapter": "Heat", "page": 1, "source": ""}


# Heat pages name conduction; reaction and education are as close in spelling, but on other pages.
HEAT_PAGES = [_chunk("Heat moves through a metal spoon by conduction. Conduction of heat needs contact.")] * 6
OTHER_PAGES = ([_chunk("A chemical reaction can give out heat."), _chunk("A chemical reaction forms new substances.")] * 3
               + [_chunk("Education helps every child. Education is a right.")] * 5
               + [_chunk("Cricket and football are games. A cricket chirps at night.")] * 3)
CHUNKS = HEAT_PAGES + OTHER_PAGES
WORDS = index_words(c["text"] for c in CHUNKS)
TEXTS = [c["text"] for c in CHUNKS]
VOCABULARY = set(WORDS)


def test_unknown_words_are_topic_words_the_index_never_uses():
    assert unknown_words("tell me about the heat endaction", WORDS) == ["endaction"]
    assert unknown_words("Tell me about heat conduction in spoons", WORDS) == []   # plural of a known word
    assert unknown_words("Please tell me about cricket", WORDS) == []
    assert unknown_words("What is it? Why? How does a fan work?", WORDS) == []     # short or question words
    assert unknown_words("mujhe heat ke baare mein bataiye", WORDS) == []


def test_the_word_offered_is_the_close_one_that_goes_with_the_other_words():
    assert closest_term("endaction", {"heat"}, VOCABULARY, TEXTS, WORDS) == "conduction"
    assert closest_term("endaction", set(), VOCABULARY, TEXTS, WORDS) is None       # nothing to go by
    assert closest_term("bitcoin", {"heat"}, VOCABULARY, TEXTS, WORDS) is None
    assert topic_phrase("tell me about the heat endaction", "endaction", "conduction") == "heat conduction"
    assert topic_phrase("What is the endaction of heat?", "endaction", "conduction") == "conduction"


class _Index:
    def __init__(self, chunks):
        self.chunks = chunks


def _engine(mt=None):
    engine = SpecialistEngine.__new__(SpecialistEngine)
    engine.index = _Index(CHUNKS)
    engine.words, engine.vocabulary = WORDS, VOCABULARY
    engine.top_k, engine.min_score = 3, 0.8
    engine.mt, engine.tts = mt, None
    engine.safety = SimpleNamespace(check=lambda text: None)
    passages = [Retrieved(0.85, c["text"], "b", "Heat", "page 1", 1, "") for c in CHUNKS]
    # The passages sharing most words with the request come first, as a search would put them.
    engine._search = lambda request, grade=None: sorted(
        passages, key=lambda p: -len(set(request.lower().split()) & set(p.text.lower().split())))
    return engine


def test_heat_endaction_asks_did_you_mean_heat_conduction():
    said = list(_engine().stream_answer("tell me about the heat endaction"))
    assert len(said) == 1 and said[0].refused
    assert said[0].text_en == "Sorry, I did not catch that. Did you mean heat conduction? Please ask again."
    assert "endaction" in said[0].reason


def test_in_hindi_the_hand_written_wording_carries_the_translated_term():
    mt = lambda text, src, tgt: {"heat conduction": "ऊष्मा चालन।"}.get(text, "?")
    said = list(_engine(mt).stream_answer("tell me about the heat endaction", target_language="hi"))
    assert said[0].text_localized == "माफ़ कीजिए, मैं ठीक से समझ नहीं पाया। क्या आपका मतलब ऊष्मा चालन था? कृपया फिर से पूछिए।"


def test_an_unknown_word_with_no_close_word_asks_again_only_if_the_rest_would_be_answered():
    engine = _engine()
    said = list(engine.stream_answer("tell me about the heat flurbixtion"))
    assert said[0].refused and said[0].text_en.startswith("Sorry, I did not catch one of the words")
    # "What is Bitcoin?" has no other word a passage shares, so it gets the normal refusal.
    said = list(engine.stream_answer("What is Bitcoin?"))
    assert said[0].refused and said[0].text_en == REFUSAL_TEXT


def test_an_off_topic_real_word_goes_on_to_the_normal_checks():
    engine = _engine()
    request = "Tell me about cricket games"
    assert engine._unclear(request, engine._search(request)) is None
