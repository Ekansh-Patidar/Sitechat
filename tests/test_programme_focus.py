"""Tests for working out which programme a visitor means.

A wrong focus means the bot is given the wrong programme's sections, so
these pin the cases that matter: names, follow-ups, switches, comparisons,
and the traps (a number that is not a programme, a general question).
"""

import pytest

from app.programmes.focus import normalise, resolve_focus
from app.programmes.models import Programme, ProgrammeCatalogue

RETREATS = ["retreat-4", "retreat-7", "retreat-14", "retreat-28"]
TTCS = ["100", "200", "300", "mysore"]

CATALOGUE = ProgrammeCatalogue(
    tenant_id="t1",
    programmes=[
        Programme(programme_id="100", name="100-Hour", aliases=["100 hour"],
                  sections=["web | 100-Hour"]),
        Programme(programme_id="200", name="200-Hour", aliases=["naturopathy"],
                  sections=["old |"]),
        Programme(programme_id="300", name="300-Hour", aliases=["300 hour", "advanced ttc"],
                  sections=["web | 300-Hour"]),
        Programme(programme_id="mysore", name="Mysore", aliases=["mysore"],
                  sections=["web | Mysore"]),
        Programme(programme_id="retreat-4", name="4-Day", aliases=["4 day retreat"],
                  context_aliases=["4 day"], sections=["web | 4-Day"]),
        Programme(programme_id="retreat-7", name="7-Day", aliases=["7 day retreat"],
                  context_aliases=["7 day"], sections=["web | 7-Day"]),
        Programme(programme_id="retreat-14", name="14-Day", aliases=["14 day retreat"],
                  context_aliases=["14 day"], sections=["web | 14-Day"]),
        Programme(programme_id="retreat-28", name="28-Day", aliases=["28 day retreat"],
                  context_aliases=["28 day"], sections=["web | 28-Day"]),
    ],
    families={"retreat": RETREATS, "retreats": RETREATS, "teacher training": TTCS},
)


def focus(message, previous=()):
    return resolve_focus(message, list(previous), CATALOGUE)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("300-hr TTC", "300 hour ttc"),
        ("300 Hours?", "300 hour"),
        ("the 7-days retreat", "the 7 day retreat"),
        ("Español", "espanol"),
    ],
)
def test_normalise(raw, expected):
    assert normalise(raw) == expected


def test_a_named_programme_becomes_the_focus():
    assert focus("Tell me about the 300-hour course") == ["300"]
    assert focus("What does the 300hrs cost?") == ["300"]


def test_a_follow_up_keeps_the_focus():
    assert focus("How much does it cost?", ["300"]) == ["300"]


def test_naming_another_programme_switches():
    assert focus("And what's included in the 7-day retreat?", ["300"]) == ["retreat-7"]


def test_a_comparison_keeps_both():
    assert focus("How does it compare with the 7-day one?", ["retreat-4"]) == [
        "retreat-4", "retreat-7"
    ]
    assert focus("What is the difference between the 4-day and the 7-day retreat?") == [
        "retreat-7", "retreat-4"
    ]


def test_a_number_alone_is_not_a_programme():
    """Trap: "7 days" is a length of stay, not the 7-day retreat."""
    assert focus("I can only come for 7 days. Which teacher training fits me?") == []


def test_a_short_name_counts_inside_its_family():
    assert focus("what about the 28 day one?", ["retreat-7"]) == ["retreat-28"]


def test_a_general_question_after_a_specific_one_clears_the_focus():
    """Trap: "what retreats do you have" is not about the 300-hour, and
    four retreats are too many to be a focus."""
    assert focus("What retreats do you have?", ["300"]) == []


def test_a_family_word_within_the_focus_keeps_it():
    assert focus("Is that retreat good for beginners?", ["retreat-7"]) == ["retreat-7"]


def test_a_small_family_becomes_the_focus():
    small = CATALOGUE.model_copy(update={"families": {"havelock": ["100", "300"]}})
    assert resolve_focus("anything on havelock?", [], small) == ["100", "300"]


def test_too_many_named_is_no_focus():
    assert focus("100 hour, 300 hour, mysore or naturopathy?") == []


def test_nothing_named_and_no_history_is_no_focus():
    assert focus("Where will I be sleeping?") == []


def test_programme_for_a_section():
    assert CATALOGUE.programme_for("web | 300-Hour > Fees") == "300"
    assert CATALOGUE.programme_for("old | Food") == "200"
    assert CATALOGUE.programme_for("web | Our teachers") == ""


def test_two_family_words_narrow_each_other():
    narrowing = CATALOGUE.model_copy(update={"families": {
        "havelock": ["mysore", "retreat-7"],
        "teacher training": TTCS,
    }})
    assert resolve_focus("the teacher training on havelock", [], narrowing) == ["mysore"]


# --- overview for groups too large to focus on --------------------------------

from app.programmes.focus import wants_overview
from app.programmes.models import OVERVIEW

WITH_OVERVIEW = CATALOGUE.model_copy(update={"overview_sections": ["web | At a glance"]})


def test_overview_for_a_group_too_large_to_focus_on():
    assert wants_overview("What retreats do you have?", [], WITH_OVERVIEW)
    assert wants_overview("Which teacher training fits me?", [], WITH_OVERVIEW)


def test_no_overview_when_there_is_a_focus_or_no_group():
    assert not wants_overview("What retreats do you have?", ["retreat-7"], WITH_OVERVIEW)
    assert not wants_overview("Is there wifi?", [], WITH_OVERVIEW)
    assert not wants_overview("What retreats do you have?", [], CATALOGUE)


def test_overview_sections_are_tagged_at_ingest():
    assert WITH_OVERVIEW.programme_for("web | At a glance") == OVERVIEW


def test_overview_for_a_comparison_within_a_group():
    assert wants_overview("Is the 7 day retreat or the 4 day retreat better?",
                          ["retreat-7", "retreat-4"], WITH_OVERVIEW)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("¿Cuánto cuesta el curso de 300 horas?", "cuanto cuesta el curso de 300 hour"),
        ("Wann beginnt der 100-Stunden-Kurs?", "wann beginnt der 100 hour kurs"),
        ("retraite de 7 jours", "retraite de 7 day"),
        ("ritiro di 14 giorni", "ritiro di 14 day"),
        ("300 घंटे का कोर्स", "300 hour"),
        ("14 दिन", "14 day"),
        ("300小时的课程", "300 hour"),
        # Not units: the word only starts the same.
        ("2 dinners and 4 diamonds", "2 dinners and 4 diamonds"),
        ("5 tags", "5 tags"),
    ],
)
def test_units_in_other_languages(raw, expected):
    assert normalise(raw) == expected


def test_a_programme_named_in_another_language_becomes_the_focus():
    assert focus("¿Cuánto cuesta el curso de 300 horas?") == ["300"]
    assert focus("Wann beginnt der 100-Stunden-Kurs?") == ["100"]
    # "14 दिन" is a short name: it counts once the retreats are the topic.
    assert focus("14 दिन के रिट्रीट में क्या शामिल है?", ["retreat-7"]) == ["retreat-14"]
