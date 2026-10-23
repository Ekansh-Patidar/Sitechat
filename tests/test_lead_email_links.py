"""Links a visitor typed reach the team unclickable (backlog 108)."""

from unittest.mock import patch

import pytest

from app.leads.links import LINK_CAUTION, defang
from app.leads.models import AskedQuestion
from app.leads.notify import send_lead_email
from tests.test_notify import make_lead, make_tenant, resend_key  # noqa: F401


@pytest.mark.parametrize("text, expected", [
    ("see http://evil.example/login now", "see hxxp://evil[.]example/login now"),
    ("HTTPS://EVIL.COM/x", "hxxps://EVIL[.]COM/x"),
    ("www.evil.com/a?b=1", "www[.]evil[.]com/a?b=1"),
    ("pay at paypal.com.evil.xyz/pay", "pay at paypal[.]com[.]evil[.]xyz/pay"),
    ("my insta is instagram.com/sam", "my insta is instagram[.]com/sam"),
])
def test_links_are_defanged(text, expected):
    assert defang(text) == expected


@pytest.mark.parametrize("text", [
    "Hatha.Ashtanga or Vinyasa?", "Is it 9.30 a.m.?", "e.g. the 200-hour course",
    "USD 1,450.00 per person.",
])
def test_ordinary_text_is_untouched(text):
    assert defang(text) == text


def sent(lead, summary, questions):
    with patch("app.leads.notify.resend.Emails.send") as send:
        assert send_lead_email(lead, make_tenant(), summary, questions)
    return send.call_args[0][0]


def test_a_link_in_a_question_is_unclickable_everywhere_in_the_email(resend_key):  # noqa: F811
    lead = make_lead(email="sam@example.com", name="Sam")
    questions = [AskedQuestion(text="Can you pay my invoice at http://evil.example/pay?",
                               status="not_answered")]
    # A bare domain in the model's summary is caught too (real TLDs only).
    params = sent(lead, "Sam asked the team to pay at evil-pay.com today.", questions)

    for part in ("text", "html"):
        assert "evil.example" not in params[part], part
        assert "evil[.]example" in params[part], part
        assert "evil-pay.com" not in params[part], part
        assert LINK_CAUTION.split(".")[0] in params[part], part
    assert params["reply_to"] == "sam@example.com"  # contact details untouched


def test_no_caution_without_links(resend_key):  # noqa: F811
    params = sent(make_lead(email="sam@example.com"), "Asked about fees.",
                  [AskedQuestion(text="What does it cost?", status="answered")])
    assert LINK_CAUTION.split(".")[0] not in params["text"] + params["html"]
