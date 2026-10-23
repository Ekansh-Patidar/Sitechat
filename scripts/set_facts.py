"""Set a tenant's structured facts.

Run from the project root:

    python -m scripts.set_facts
"""

import logging

from app.config import get_settings
from app.facts.models import Fact, FactSheet
from app.facts.store import FirestoreFactStore
from app.logging_setup import setup_logging


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    sheet = FactSheet(
        tenant_id="pyramid-yogshala",
        facts=[
            Fact(
                category="Courses and fees",
                label="200-hour Teacher Training fee",
                value="USD 1,450, including shared accommodation and all meals",
            ),
            Fact(
                category="Courses and fees",
                label="Private room supplement",
                value="USD 350 for the full course",
            ),
            Fact(
                category="Dates",
                label="Upcoming 200-hour course dates",
                value="2 to 28 March 2027, and 4 to 30 April 2027",
            ),
            Fact(
                category="Dates",
                label="Booking deposit",
                value="USD 300, non-refundable, due to confirm a place",
            ),
            Fact(
                category="Food and accommodation",
                label="Meals",
                value=(
                    "Three vegetarian sattvic meals a day. Vegan and "
                    "gluten-free options available on request."
                ),
            ),
            Fact(
                category="Practical",
                label="Location",
                value="Rishikesh, Uttarakhand, India",
            ),
            Fact(
                category="Practical",
                label="Certification",
                value="Yoga Alliance registered, RYT 200",
            ),
        ],
    )

    store = FirestoreFactStore(settings.gcp_project_id)
    store.save(sheet)
    logging.getLogger(__name__).info("Saved %d facts", len(sheet.facts))


if __name__ == "__main__":
    main()