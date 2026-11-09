"""One clean question out of a noisy OCR dump of a textbook page."""

from pocketinfer.specialist.page_questions import (
    clean_ocr, extract_questions_from_ocr, question_from_page, select_question,
)

ENGLISH_PAGE = """12
Chapter 3 Nutrition in Plants

Plants make their own food. This process is called photo-
synthesis and it takes place in the leaves.

Fig. 3.1 A leaf in sunlight

EXERCISES
1. What is photosynthesis?
2. Why are leaves green and how does chlorophyll
help the plant?
3) Define autotroph.
Q4. Explain the role of Al in farming.
Page 12
"""

HINDI_PAGE = """४२
अध्याय 3 पौधों में पोषण

पौधे अपना भोजन स्वयं बनाते हैं |
चित्र 3.1 एक पत्ती

प्रश्न 1. प्रकाश संश्लेषण क्या है?
2. पत्तियाँ हरी क्यों होती हैं?
3. स्वपोषी और परपोषी में अंतर समझाइए।
"""


def test_page_furniture_is_removed():
    lines = clean_ocr(ENGLISH_PAGE)
    assert "12" not in lines and not any(line.startswith(("Fig.", "Page", "Chapter")) for line in lines)


def test_ligatures_broken_words_and_ai_are_repaired():
    assert clean_ocr("The ﬁrst photo-\nsynthesis of Al")[0].startswith("The first photosynthesis of AI")


def test_aluminium_is_not_turned_into_ai():
    assert "Al" in clean_ocr("The metal Al reacts with acid.")[0].split()


def test_english_questions_are_separated():
    found = extract_questions_from_ocr(ENGLISH_PAGE)
    assert found == ["What is photosynthesis?", "Why are leaves green and how does chlorophyll help the plant?",
                     "Define autotroph.", "Explain the role of AI in farming."]


def test_hindi_questions_are_separated():
    found = extract_questions_from_ocr(HINDI_PAGE)
    assert found == ["प्रकाश संश्लेषण क्या है?", "पत्तियाँ हरी क्यों होती हैं?", "स्वपोषी और परपोषी में अंतर समझाइए।"]


def test_a_statement_is_not_taken_for_a_question():
    assert extract_questions_from_ocr("Plants make their own food. Leaves are green.") == []


def test_the_selector_is_deterministic_and_honours_an_index():
    questions = ["a b?", "c d?", "e f?", "g h?"]
    assert select_question(questions) == "c d?"
    assert select_question(questions, 3) == "g h?"
    assert select_question(questions, 9) is None
    assert select_question([]) is None


def test_exactly_one_question_goes_on():
    assert question_from_page(ENGLISH_PAGE) == "Why are leaves green and how does chlorophyll help the plant?"
    assert question_from_page(ENGLISH_PAGE, 0) == "What is photosynthesis?"
    assert question_from_page("Plants make their own food.") == "Plants make their own food."
