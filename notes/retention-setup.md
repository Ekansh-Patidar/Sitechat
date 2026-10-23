# Setting up automatic deletion (retention)

Conversations, leads and feedback hold visitors' personal data, or point to it. Every time one is written, the service stamps it with `expire_at`: `retention_days` (365, `app/config.py`) after its last activity. A Firestore TTL policy on that field deletes the document once the date has passed, usually within a day. Nothing is deleted until the policies below exist.

## One-time setup

Run once, from a terminal logged in to the project. Each takes a few minutes to become active.

```
gcloud firestore fields ttls update expire_at --collection-group=conversations --enable-ttl
gcloud firestore fields ttls update expire_at --collection-group=leads --enable-ttl
gcloud firestore fields ttls update expire_at --collection-group=feedback --enable-ttl
```

(The `feedback` one was added on 2026-10-18 with thumbs up/down; the first two were already active.)

Check:

```
gcloud firestore fields ttls list
```

## What it does and does not do

- A conversation or lead with activity is kept for another year from that activity; one that goes quiet is deleted a year later.
- Documents written before this change have no `expire_at` and are kept until they are next written. The demo data is all test data; delete it with `python -m scripts.delete_tenant` if it should go too.
- Counters (`lead_email_counts`, `token_usage`) hold no personal data and are not covered.
- To delete one visitor's data on request: `python -m scripts.delete_tenant <tenant> --conversation <id>` (dry run first).
- To change the period, change `retention_days` and deploy; each document takes the new date at its next write. 0 stops stamping new dates (existing ones still expire).

## Cost

TTL deletions count as Firestore deletes (20,000 a day free). Far more than this needs.
