"""Tenant.wording: how the bot describes the business.

The defaults must keep the first customer, a yoga school, exactly as it
was; another business must get no yoga wording at all.
"""

from app.agent.prompts import build_system_prompt
from app.agent.tools import tool_schemas
from app.leads.email_html import render_lead_email
from app.leads.models import Lead
from app.leads.notify import _format_body
from app.tenants.models import Tenant, Wording


def make_tenant(**fields) -> Tenant:
    return Tenant(tenant_id="t1", site_key="pk_t1", display_name="Test School", **fields)


def words(text: str) -> str:
    """Line breaks inside a sentence are not wording."""
    return " ".join(text.split())


# The yoga school's phrases, as they were before wording was configurable.
YOGA_PHRASES = [
    "Help only with questions about Test School: its courses and retreats, "
    "staying there, getting there, and booking.",
    "The business offers several courses and retreats.",
    "Sections about the school as a whole (About, Contact, Booking, Our "
    "teachers, Certification) apply to every programme.",
    "If the answer depends on which course or retreat the visitor means",
    "If they are unsure which to choose, ask about their experience, how "
    "long they can stay and where they would like to be, then suggest the "
    "programmes that fit.",
    "You are speaking to prospective students, often from other countries.",
    "or which course interests them, call record_visitor_details",
    "ask once, in a single friendly message, where they are joining from "
    "and roughly when they are hoping to come. If they would rather not say",
]

GUESTHOUSE = Wording(
    topics="its rooms, meals, local trips, getting there, and booking",
    offerings="rooms and packages",
    offering="room or package",
    the_business="the guesthouse",
    general_sections="About, Contact, Booking, House rules",
    choosing="ask about their dates, how many are travelling and their budget",
    audience="travellers planning a stay",
    plans="where they are travelling from and roughly when they plan to stay",
    interest="room",
    when_label="Planning to stay",
)


def test_default_wording_is_the_yoga_schools_unchanged():
    prompt = words(build_system_prompt(make_tenant()))

    for phrase in YOGA_PHRASES:
        assert phrase in prompt


def test_another_business_gets_its_own_wording_and_none_of_the_yoga():
    prompt = words(build_system_prompt(make_tenant(wording=GUESTHOUSE)))

    assert "about Test School: its rooms, meals, local trips" in prompt
    assert "You are speaking to travellers planning a stay." in prompt
    assert "which room or package the visitor means" in prompt
    # What describes the business. "courses" survives only in two general
    # examples ("never invent details about courses, dates, prices...").
    for yoga in ("retreat", "prospective students", "school as a whole",
                 "Certification", "joining from", "how long they can stay"):
        assert yoga not in prompt


def test_tool_descriptions_follow_the_wording():
    default = str(tool_schemas(make_tenant()))
    guesthouse = str(tool_schemas(make_tenant(wording=GUESTHOUSE)))

    assert "which course they are interested in" in default
    assert "Which course they asked about." in default
    assert "which room they are interested in" in guesthouse
    # The field keeps its stored name, course_interest; only its wording changes.
    assert "course" not in guesthouse.replace("course_interest", "")


def test_lead_email_label_follows_the_wording():
    lead = Lead(tenant_id="t1", conversation_id="c1", email="a@example.com",
                intended_month="May")

    yoga = make_tenant(team_email="team@example.com")
    stay = make_tenant(team_email="team@example.com", wording=GUESTHOUSE)

    assert "Planning to join: May" in _format_body(lead, yoga, "s")
    assert "Planning to stay: May" in _format_body(lead, stay, "s")
    assert "Planning to stay" in render_lead_email(lead, stay, "s")


def test_a_stored_tenant_without_wording_gets_the_defaults():
    """Tenants saved before wording existed load unchanged."""
    stored = {"tenant_id": "t1", "site_key": "pk_t1", "display_name": "X"}

    assert Tenant(**stored).wording == Wording()
