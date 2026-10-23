"""Tests for recording visitor questions.

Which messages count as questions decides what reaches the team, so the
check errs towards including. These pin both sides: real questions,
including ones without a question mark, and the filler it must skip.
"""

import pytest

from app.agent.questions import is_visitor_question
from app.conversations.models import REPLY_LIMIT
from app.conversations.store import InMemoryConversationStore


@pytest.mark.parametrize(
    "message",
    [
        "how long is the course for?",
        "okay, what about exam, is there an exaam at end?",
        "what payment methods do you accept",
        "is there an age limit",
        "okay, is the property LGBTQ friendly",
        "Can i get a internship here",
        "please share the fees",
        "I want to know about accommodation",
        "I did my 200 hours in the UK and want to do the 300 hour one",
        "[email provided] what about the dates?",
        # Other languages: their question marks, or long enough to be about
        # something even though the English word lists do not apply.
        "コースはいつ始まりますか？",
        "كم سعر الدورة؟",
        "¿Cuándo empieza el curso",
        "Quisiera saber las fechas del curso de doscientas horas",
        "मुझे दो सौ घंटे के कोर्स की फीस और तारीखें जाननी हैं",
    ],
)
def test_questions_are_recorded(message):
    assert is_visitor_question(message)


@pytest.mark.parametrize(
    "message",
    [
        "okay",
        "Okay!",
        "hi",
        "thanks",
        "No that's it, thanks",
        "[email provided]",
        "[email provided], but i have few more queries",
        "I want to connect with a team",
        "",
        "gracias",
        "धन्यवाद",
        "merci beaucoup",
    ],
)
def test_filler_is_not_recorded(message):
    assert not is_visitor_question(message)


def test_question_is_stored_with_its_reply_in_the_same_turn():
    store = InMemoryConversationStore()
    store.append_turn("t1", "c1", "is there an exam?", "x" * 900, is_question=True)
    store.append_turn("t1", "c1", "okay", "Great.", is_question=False)

    convo = store.get("t1", "c1")
    assert len(convo.messages) == 4
    assert [q.text for q in convo.questions] == ["is there an exam?"]
    assert len(convo.questions[0].reply) == REPLY_LIMIT


def test_summarising_messages_keeps_every_question():
    store = InMemoryConversationStore()
    for i in range(4):
        store.append_turn("t1", "c1", f"question {i}?", "answer", is_question=True)
    convo = store.get("t1", "c1")

    store.apply_summary("t1", "c1", "summary", list(convo.messages[:6]))

    convo = store.get("t1", "c1")
    assert len(convo.messages) == 2
    assert len(convo.questions) == 4


def test_questions_emailed_never_moves_backwards():
    store = InMemoryConversationStore()
    store.append_turn("t1", "c1", "a?", "r", is_question=True)

    store.mark_questions_emailed("t1", "c1", 3)
    store.mark_questions_emailed("t1", "c1", 1)

    assert store.get("t1", "c1").questions_emailed == 3
