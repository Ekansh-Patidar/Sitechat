"""Tests for the faithfulness judge.

The model call is mocked. What is testable here is the parsing: a judge that
returns something unexpected must not crash the run, and a fenced JSON reply
must still be read correctly, because models do that often enough to matter.
"""

from unittest.mock import patch

from evaluation.faithfulness import judge_grounded, judge_refusal


@patch("evaluation.faithfulness.complete")
def test_supported_answer_passes(mock_complete):
    mock_complete.return_value = (
        '{"supported": true, "unsupported_claims": [], "reason": "all backed"}'
    )

    result = judge_grounded("q", "a", ["chunk text"])

    assert result.passed is True
    assert result.detail == "all backed"


@patch("evaluation.faithfulness.complete")
def test_unsupported_claims_are_captured(mock_complete):
    mock_complete.return_value = (
        '{"supported": false, "unsupported_claims": ["invented a price"], '
        '"reason": "price not in extracts"}'
    )

    result = judge_grounded("q", "a", ["chunk text"])

    assert result.passed is False
    assert result.unsupported_claims == ["invented a price"]


@patch("evaluation.faithfulness.complete")
def test_fenced_json_is_parsed(mock_complete):
    """Models wrap JSON in code fences despite instructions not to."""
    mock_complete.return_value = (
        '```json\n{"supported": true, "unsupported_claims": [], '
        '"reason": "fine"}\n```'
    )

    result = judge_grounded("q", "a", ["chunk"])

    assert result.passed is True


@patch("evaluation.faithfulness.complete")
def test_unparseable_output_fails_safely(mock_complete):
    """A broken judge reply must not crash the run, and must not pass."""
    mock_complete.return_value = "I think the answer looks fine to me."

    result = judge_grounded("q", "a", ["chunk"])

    assert result.passed is False
    assert "judge failed" in result.detail


@patch("evaluation.faithfulness.complete")
def test_refusal_judge(mock_complete):
    mock_complete.return_value = (
        '{"reason": "said unknown", "claimed_an_answer": false}'
    )

    result = judge_refusal("can I bring my dog?", "I do not have that detail.")

    assert result.passed is True


@patch("evaluation.faithfulness.complete")
def test_refusal_judge_fails_an_invented_answer(mock_complete):
    mock_complete.return_value = (
        '{"reason": "said dogs are allowed", "claimed_an_answer": true}'
    )

    result = judge_refusal("can I bring my dog?", "Yes, dogs are welcome.")

    assert result.passed is False


@patch("evaluation.faithfulness.complete", return_value="not json")
def test_refusal_judge_without_a_verdict_fails(_):
    assert judge_refusal("q", "a").passed is False

@patch("evaluation.faithfulness.complete")
def test_grounding_judge_sees_facts_history_and_the_mixing_rule(mock_complete):
    mock_complete.return_value = '{"supported": true, "reason": "ok"}'

    judge_grounded(
        "How much does it cost?", "US $1199.", ["300-Hour > Fees\ntext"],
        facts="Fees: 300-hour TTC:\n- Private room: US $1199",
        history=["Tell me about the 300-hour course"],
    )

    system, user = mock_complete.call_args[0][0]
    assert "Mixing programmes up" in system["content"]
    assert "VERIFIED FACTS:\nFees: 300-hour TTC" in user["content"]
    assert "EARLIER VISITOR MESSAGES:\n- Tell me about the 300-hour course" in user["content"]
