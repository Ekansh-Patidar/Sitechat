"""A tenant's programmes: its courses and retreats, and how visitors name them.

The catalogue is written per tenant (docs/<tenant>/programmes.yaml), stored
at ingest, and used twice:
  - at ingest, to tag each chunk with the programme its section belongs to
  - per message, to work out which programme the visitor is asking about

Sections use the label format of app/documents/sections.py.
"""

from pydantic import BaseModel, Field

from app.documents.sections import matches

# The tag for overview sections, in place of a programme id.
OVERVIEW = "overview"


class Programme(BaseModel):
    programme_id: str
    name: str
    # Names visitors use, e.g. "300 hour", "advanced ttc". Matched after
    # normalising ("300-hr" and "300 hours" both read "300 hour").
    aliases: list[str] = Field(default_factory=list)
    # Short names that only count once the conversation is already about a
    # programme in the same family, e.g. "7 day" for the 7-day retreat. On
    # their own they are too ambiguous: "I can only come for 7 days".
    context_aliases: list[str] = Field(default_factory=list)
    # The knowledge base sections that belong to this programme.
    sections: list[str] = Field(default_factory=list)


class ProgrammeCatalogue(BaseModel):
    tenant_id: str
    programmes: list[Programme] = Field(default_factory=list)
    # Words that name a group of programmes, e.g. "retreat" -> every retreat.
    families: dict[str, list[str]] = Field(default_factory=dict)
    # Sections that compare all programmes side by side ("Programmes at a
    # glance"). Tagged OVERVIEW, and fetched when a visitor names a group
    # too large to focus on, so every course's length and place is in view.
    overview_sections: list[str] = Field(default_factory=list)

    def programme_for(self, key: str) -> str:
        """The programme a section key belongs to: OVERVIEW, an id, or ""."""
        if any(matches(key, label) for label in self.overview_sections):
            return OVERVIEW
        for programme in self.programmes:
            if any(matches(key, label) for label in programme.sections):
                return programme.programme_id
        return ""

    def get(self, programme_id: str) -> Programme | None:
        for programme in self.programmes:
            if programme.programme_id == programme_id:
                return programme
        return None


def load_catalogue(path, tenant_id: str) -> ProgrammeCatalogue:
    """Read a tenant's programmes.yaml."""
    import yaml

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return ProgrammeCatalogue(
        tenant_id=tenant_id,
        programmes=[Programme(**p) for p in data.get("programmes", [])],
        families=data.get("families", {}),
        overview_sections=data.get("overview_sections", []),
    )
