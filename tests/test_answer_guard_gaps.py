"""Two known answer gaps: a name paired with an invention it did not make, and how-to requests
for everyday tasks that share one word with a lesson."""

import pytest

from pocketinfer.specialist.answer_guard import contradicts, unsupported_credit
from pocketinfer.specialist.grounding import asks_how_to, build_grounding
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
