"""The deterministic half of the attack evaluation (evaluation/attacks.py):
no network, so it runs with the suite."""

from app.agent.prompts import BASE_RULES, build_context_message, build_facts_message
from evaluation.attacks import ATTACKS, LEAK_MARKERS, Attack, check_reply

ATTACK = Attack("output", "x", "y", forbid=["<script"], max_chars=50)


def test_a_clean_short_reply_passes():
    assert check_reply(ATTACK, "Happy to help with our courses!") == []


def test_forbidden_text_fails_whatever_its_case():
    assert check_reply(ATTACK, "<SCRIPT>alert(1)</SCRIPT>") == ["contains '<script'"]


def test_a_long_reply_fails():
    assert "limit 50" in check_reply(ATTACK, "yoga " * 20)[0]


def test_leaked_prompt_text_fails():
    assert check_reply(ATTACK, "My rules say: call contact_team.")[0].startswith("leaked")


def test_every_leak_marker_is_really_in_the_hidden_context():
    """If the prompt is reworded, the markers must follow, or leaks go unseen."""
    hidden = (BASE_RULES + build_facts_message_text() + build_context_message_text())
    missing = [m for m in LEAK_MARKERS if m not in hidden]
    assert not missing


def test_the_attack_set_covers_every_category():
    assert {a.category for a in ATTACKS} == {
        "off_topic", "extraction", "jailbreak", "false_facts", "data", "output", "consumption",
    }


def build_facts_message_text():
    from app.facts.models import Fact, FactSheet
    return build_facts_message(FactSheet(tenant_id="t", facts=[Fact(category="C", label="L", value="V")]))


def build_context_message_text():
    result = type("R", (), {"citation_title": "T", "text": "x"})()
    return build_context_message([result])


def test_planted_text_in_a_tool_call_fails():
    attack = Attack("output", "x", "y", forbid=["evil.example"])
    assert check_reply(attack, "Done!", tool_text="call me; see evil.example/login") == [
        "tool call (to the team) contains 'evil.example'"
    ]
