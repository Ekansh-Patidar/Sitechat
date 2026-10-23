# Setting up the lead digest

The lead digest emails the team about a visitor who gave contact details, once they have been quiet for 15 minutes (`lead_digest_idle_minutes`), even if they never asked to be contacted. Cloud Scheduler calls `POST /internal/lead-digest` every 5 minutes; each call sends what is due (`app/agent/orchestrator.py`, `send_due_digests`).

The same call also keeps each tenant's facts current: any tenant whose facts are more than a day old (`facts_sync_hours`) is re-read from its Google Sheet, two tenants per call at most (`app/facts/sync.py`, `sync_stale_facts`). For that, share each tenant's facts sheet, as **Viewer**, with the service's own account, `sitechat-runtime@PROJECT_ID.iam.gserviceaccount.com` (the local `service-account.json` is not in production). Until it is shared, the sync logs an error once an hour and the bot keeps the facts it has. A sheet that suddenly has no facts, or has lost more than half of them, is not applied: the old facts stay and an error is logged; run `python -m scripts.sync_facts <tenant>` by hand to accept such a change.

The endpoint accepts only requests signed by Google for the `lead-digest` service account, with the audience `sitechat-lead-digest`. `cloudbuild.yaml` sets `LEAD_DIGEST_INVOKER` to that account; until the account and the job below exist, nothing calls it and nothing else can.

## One-time setup

Run these once, from a terminal logged in to the project (`gcloud config get-value project` shows it). Replace `PROJECT_ID`.

1. Enable Cloud Scheduler:

   ```
   gcloud services enable cloudscheduler.googleapis.com
   ```

2. Create the service account the scheduler signs its requests as. It needs no roles: the endpoint only checks who signed the token.

   ```
   gcloud iam service-accounts create lead-digest --display-name="Sitechat lead digest"
   ```

3. Create the job, every 5 minutes:

   ```
   gcloud scheduler jobs create http sitechat-lead-digest \
     --location=us-central1 \
     --schedule="*/5 * * * *" \
     --uri="https://sitechat-api-uukevi3qpq-uc.a.run.app/internal/lead-digest" \
     --http-method=POST \
     --oidc-service-account-email="lead-digest@PROJECT_ID.iam.gserviceaccount.com" \
     --oidc-token-audience="sitechat-lead-digest" \
     --attempt-deadline=60s
   ```

   If this says you are not allowed to act as the service account, grant yourself `roles/iam.serviceAccountUser` on it:

   ```
   gcloud iam service-accounts add-iam-policy-binding \
     lead-digest@PROJECT_ID.iam.gserviceaccount.com \
     --member="user:YOUR_GOOGLE_EMAIL" --role="roles/iam.serviceAccountUser"
   ```

4. Deploy (push), so the service has `LEAD_DIGEST_INVOKER` set.

## Checking it works

- Run the job once by hand: `gcloud scheduler jobs run sitechat-lead-digest --location=us-central1`
- In Logs Explorer, look for `Lead digest sweep: {'due': ..., 'sent': ...}`. A 401 or 403 in the job's log means the token was refused: check the account name and the audience.
- End to end: in a new chat, ask two questions, give your email, ask one more, and stop. The email should arrive 15 to 20 minutes later, listing all three questions.

## Cost

Without any free tier: about ₹20–25 a month in total, for all tenants (one job at $0.10, about 8,640 short requests, one small Firestore query each), plus about ₹0.06 per email for the summary. Calls every 5 minutes also keep the service from scaling to zero, so visitors rarely meet a cold start; with request-based billing an idle instance is not charged. Check this on the first month's bill.

## Turning it off

`gcloud scheduler jobs pause sitechat-lead-digest --location=us-central1` stops the sweeps; contact requests still email immediately as before.
