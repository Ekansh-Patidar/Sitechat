"""Create or update a tenant.

Onboarding is manual for now, so this script is the admin interface. Run it
from the project root:

    python -m scripts.create_tenant
"""

import logging

from app.config import get_settings
from app.logging_setup import setup_logging
from app.tenants.models import Tenant
from app.tenants.store import FirestoreTenantStore


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    tenant = Tenant(
        tenant_id="pyramid-yogshala",
        site_key="pk_pyramid_yogshala_001",
        display_name="Pyramid Yogshala",
        allowed_domains=[
            "localhost",
            "127.0.0.1",
            "sitechat-api-uukevi3qpq-uc.a.run.app",
        ],
        whatsapp_number="910000000000",
        greeting="Namaste! Ask me anything about our teacher training courses.",
        suggested_questions=[
            "What is covered in the 200-hour course?",
            "What are the dates and fees?",
            "What food is provided?",
            "Who are the teachers?",
        ],
        website_url="https://example.com",
        facts_sheet_id="1kzJmt47FLJ8ieyg4o8ccZm2CXNlrw5UgT2RGVIBMHYI",
        team_email="sandeep.dot4@gmail.com",
        reply_time="within 24 hours",
        accent_colour="#8a6d3b",
        # A yoga school uses the default wording. Another kind of business
        # sets its own; each phrase slots into a sentence (see Wording in
        # app/tenants/models.py; import it from there), e.g. for a guesthouse:
        # wording=Wording(
        #     topics="its rooms, meals, local trips, getting there, and booking",
        #     offerings="rooms and packages",
        #     offering="room or package",
        #     the_business="the guesthouse",
        #     general_sections="About, Contact, Booking, House rules",
        #     choosing="ask about their dates, how many are travelling and their budget",
        #     audience="travellers planning a stay",
        #     plans="where they are travelling from and roughly when they plan to stay",
        #     interest="room",
        #     when_label="Planning to stay",
        # ),
    )

    store = FirestoreTenantStore(settings.gcp_project_id)
    store.save(tenant)
    logging.getLogger(__name__).info("Done: %s", tenant.tenant_id)


if __name__ == "__main__":
    main()