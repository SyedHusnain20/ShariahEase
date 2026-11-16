"""The keyword rule behind the riba / doubtful reminders (offline, no database)."""
import pytest

from app.services import shariah_flags as sf
from app.services.shariah_flags import flag_for, keyword_flag, normalise, stronger


@pytest.mark.parametrize("note", [
    "bank interest", "Interest", "INTEREST on card", "interests paid", "usury", "riba payment", "ribaa",
    "sood", "SOOD ki qist", "soodi qarz", "sud", "bank ka sood, March",
    "سود", "سودی قرض", "بینک کا سود", "سود،", "سود۔", "سود؟", "ربا", "ربوا", "سُود", "ﺳﻮﺩ",
    "conflict of interest", "(sood)", "bank_interest", "ribā", "interest.",
])
def test_words_that_mean_interest_raise_riba(note):
    assert keyword_flag(note) == "riba"


@pytest.mark.parametrize("note", ["markup", "Mark up 10%", "mark-up", "mark_up on goods", "مارک اپ", "مارکاپ"])
def test_markup_alone_is_only_doubtful(note):
    assert keyword_flag(note) == "doubtful"


def test_interest_beats_markup_in_the_same_note():
    assert keyword_flag("markup and interest") == "riba"
    assert keyword_flag("interest markup") == "riba"


@pytest.mark.parametrize("note", [
    "interesting book", "interested in cars", "disinterest", "interestingly", "sauda", "سودا سلف", "سودا", "ربانی",
    "sudden", "pseudo", "sudoku", "foods", "ribald", "", "   ", "groceries", "salary", "school fee", "markups", "bookmarkup",
])
def test_words_that_only_contain_the_letters_do_not_match(note):
    assert keyword_flag(note) is None


@pytest.mark.parametrize("note", [
    "interest-free loan", "interest free", "Interest   Free", "interestfree", "no interest", "without interest",
    "zero interest", "riba free", "bila sood qarz", "bilasood", "sood se paak", "sood free", "baghair sood",
    "بلا سود قرض", "بلاسود", "بغیر سود کے", "سود سے پاک", "سود فری", "بلا ربا",
])
def test_phrases_that_say_there_is_no_interest_do_not_flag(note):
    assert keyword_flag(note) is None


def test_a_negation_only_removes_its_own_words():
    assert keyword_flag("interest free loan and bank interest") == "riba"
    assert keyword_flag("بلا سود قرض اور بینک کا سود") == "riba"


@pytest.mark.parametrize("value", [None, 0, 12, b"interest", ["interest"], {"interest": 1}])
def test_a_note_that_is_not_text_never_flags_or_crashes(value):
    assert keyword_flag(value) is None


def test_normalise_makes_spellings_comparable():
    assert normalise("  Mark-Uṕ ") == "mark up"
    assert normalise("سُود") == "سود"                       # vowel mark removed
    assert normalise("سـود") == "سود"                       # tatweel removed
    assert normalise("ﻛﺜﻴﺮ") == normalise("كثير")           # presentation forms and Arabic kaf/yeh meet Urdu spelling
    assert normalise("A_B-C") == "a b c"


def test_stronger_ranks_riba_over_doubtful_over_nothing():
    assert stronger(None, None) is None
    assert stronger("doubtful", None) == "doubtful" and stronger(None, "doubtful") == "doubtful"
    assert stronger("riba", "doubtful") == "riba" and stronger("doubtful", "riba") == "riba"
    assert stronger("riba", "riba") == "riba" and stronger("doubtful", "doubtful") == "doubtful"
    assert stronger("haram", None) is None and stronger("haram", "doubtful") == "doubtful"      # unknown values count as nothing


def test_flag_for_keeps_the_subcategory_default_and_lets_a_stronger_note_raise_it():
    assert flag_for(None, "groceries") is None
    assert flag_for("riba", "groceries") == "riba"                   # a default is never lowered by an innocent note
    assert flag_for("riba", "bila sood") == "riba"                   # not even by a "no interest" note
    assert flag_for("doubtful", "bank interest") == "riba"           # a stronger note raises it
    assert flag_for(None, "markup") == "doubtful"
    assert flag_for("doubtful", "markup") == "doubtful"


def test_every_listed_term_is_matched_as_a_whole_word():
    for term in sf.STRONG_TERMS:
        assert keyword_flag(term) == "riba", term
        assert keyword_flag(f"x {term} y") == "riba", term
    for term in sf.WEAK_TERMS:
        assert keyword_flag(term) == "doubtful", term
    for term in sf.NEGATIONS:
        assert keyword_flag(term) is None, term


def test_glued_letters_never_match():
    for glued in ("xinterest", "interestx", "1riba", "ribax", "xsood", "soodx", "ٹسود", "سودٹ"):
        assert keyword_flag(glued) is None, glued


def test_the_word_lists_are_plain_tuples_a_person_can_extend():
    for name in ("STRONG_TERMS", "WEAK_TERMS", "NEGATIONS"):
        assert isinstance(getattr(sf, name), tuple) and getattr(sf, name)
    assert len(sf.STRONG_TERMS) + len(sf.WEAK_TERMS) < 30               # a small list on purpose


def test_arabic_yeh_and_kaf_typed_on_an_arabic_keyboard_meet_the_urdu_spelling():
    assert keyword_flag("سودي") == "riba"                      # سودي with Arabic yeh (U+064A); the listed word has Urdu yeh
    assert keyword_flag("سودى") == "riba"                      # with alef maksura (U+0649)
    assert keyword_flag("مارك اپ") == "doubtful"     # مارك اپ with Arabic kaf (U+0643)
    assert normalise("كيى") == "کیی"


def test_the_letter_heh_u06d5_counts_as_part_of_a_word():
    assert keyword_flag("سودە") is None                        # سودە is a different word, not سود
    assert keyword_flag("سود ە") == "riba"                     # a separate letter after a space does not glue
