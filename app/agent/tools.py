"""Tools the model can call.

Two, and the split matters. contact_team is an action: it records a request
that the server emails to the team after the reply.
record_visitor_details only reports what the model noticed, so the server
can store it: the model is being used for extraction, not given authority.

There is no tool for unanswered questions. One existed, and the model
often said it was recording a question without calling it. They are now
extracted from the whole conversation when the lead email is written.
"""

from app.tenants.models import Tenant, Wording

CONTACT_TEAM_TOOL = {
    "type": "function",
    "function": {
        "name": "contact_team",
        "description": (
            "Pass this visitor's request to the team, who will follow up by "
            "email. Use it when the visitor wants the team to get in touch: "
            "they ask to speak to someone, or accept your offer. Call it "
            "even before you have their email address; the result tells you "
            "whether to ask for it. Do not use it for questions you can "
            "answer, or again just because the visitor says thanks or "
            "goodbye."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": (
                        "One sentence: what the visitor wants the team to "
                        "follow up on, in their words where possible. "
                        "Questions you could not answer are collected "
                        "separately, so do not list them all here."
                    ),
                },
            },
            "required": ["reason"],
        },
    },
}


def record_details_tool(wording: Wording) -> dict:
    """Worded for the tenant's business: a yoga school's course, a
    hotel's room, and so on (Tenant.wording)."""
    return {
        "type": "function",
        "function": {
            "name": "record_visitor_details",
            "description": (
                "Record details the visitor has mentioned about themselves. Call "
                "this whenever they tell you their name, where they are from, "
                "which month they are planning to come, or which "
                f"{wording.interest} they are interested in. Only include what "
                "they actually said. Never guess."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "country": {"type": "string"},
                    "intended_month": {
                        "type": "string",
                        "description": "e.g. 'March 2027' or 'sometime next spring'",
                    },
                    "course_interest": {
                        "type": "string",
                        "description": f"Which {wording.interest} they asked about.",
                    },
                },
                "required": [],
            },
        },
    }


def tool_schemas(tenant: Tenant) -> list[dict]:
    return [CONTACT_TEAM_TOOL, record_details_tool(tenant.wording)]
