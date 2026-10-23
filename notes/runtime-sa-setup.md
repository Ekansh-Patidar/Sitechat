# Setting up the runtime service account

The API on Cloud Run runs as `sitechat-runtime`, an account that can only read and write Firestore and read its five secrets. Before this, it ran as the default compute account, which has Editor on the whole project: anyone who found a hole in the app could have done almost anything in GCP with it.

`cloudbuild.yaml` deploys with `--service-account=sitechat-runtime@...`. **Run the steps below before pushing that change.** If the account does not exist yet, the deploy step fails and the current revision keeps serving (no outage, but no deploy either).

What the service uses, and so all it is given:

| Needs | Role | Why |
|---|---|---|
| Firestore | `roles/datastore.user` (project) | tenants, conversations, leads, facts, email counts |
| The five secrets | `roles/secretmanager.secretAccessor` (on each secret only) | `--set-secrets` in `cloudbuild.yaml` |

Not needed: logging (Cloud Run collects stdout itself), Pinecone, OpenAI, Resend, Cohere and Sentry (API keys, not GCP), the lead-digest token check (Google's public keys, no IAM), and Google Sheets (a sheet is shared with the account, not granted by a role: share each tenant's facts sheet with `sitechat-runtime@PROJECT_ID.iam.gserviceaccount.com` as Viewer for the daily sync, backlog 65).

## One-time setup

Run these from a terminal logged in to the project (`gcloud config get-value project` shows `sitechat-511008`). Each is one line, so they work in PowerShell, cmd or bash.

1. Create the account:

   ```
   gcloud iam service-accounts create sitechat-runtime --display-name="Sitechat API (Cloud Run)"
   ```

2. Let it use Firestore:

   ```
   gcloud projects add-iam-policy-binding sitechat-511008 --member="serviceAccount:sitechat-runtime@sitechat-511008.iam.gserviceaccount.com" --role="roles/datastore.user" --condition=None
   ```

3. Let it read each secret (five commands):

   ```
   gcloud secrets add-iam-policy-binding OPENAI_API_KEY --member="serviceAccount:sitechat-runtime@sitechat-511008.iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor"
   gcloud secrets add-iam-policy-binding PINECONE_API_KEY --member="serviceAccount:sitechat-runtime@sitechat-511008.iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor"
   gcloud secrets add-iam-policy-binding COHERE_API_KEY --member="serviceAccount:sitechat-runtime@sitechat-511008.iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor"
   gcloud secrets add-iam-policy-binding RESEND_API_KEY --member="serviceAccount:sitechat-runtime@sitechat-511008.iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor"
   gcloud secrets add-iam-policy-binding SENTRY_DSN --member="serviceAccount:sitechat-runtime@sitechat-511008.iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor"
   ```

4. Let Cloud Build deploy a service that runs as it. First find which account your builds run as:

   ```
   gcloud builds list --limit=1 --format="value(serviceAccount)"
   ```

   If that prints nothing, add `--region=us-central1`. It prints something like `projects/sitechat-511008/serviceAccounts/123456789-compute@developer.gserviceaccount.com`; the part after `serviceAccounts/` is the email. Then (replace `BUILD_ACCOUNT_EMAIL`):

   ```
   gcloud iam service-accounts add-iam-policy-binding sitechat-runtime@sitechat-511008.iam.gserviceaccount.com --member="serviceAccount:BUILD_ACCOUNT_EMAIL" --role="roles/iam.serviceAccountUser"
   ```

   (If the build account is the default compute account with Editor, it can already do this; the grant makes it explicit and keeps working if Editor is removed later.)

5. Push. The build runs the tests, then deploys with the new account.

## Checking it works

- The service runs as the new account:

  ```
  gcloud run services describe sitechat-api --region=us-central1 --format="value(spec.template.spec.serviceAccountName)"
  ```

  should print `sitechat-runtime@sitechat-511008.iam.gserviceaccount.com`.
- `python -m scripts.smoke_test <service url> pk_pyramid_yogshala_001` passes (reads the tenant from Firestore).
- One chat on the demo page gets a reply (Firestore, secrets, OpenAI, Pinecone all working).
- In Logs Explorer, the next `Lead digest sweep:` line appears with no errors (Firestore queries work).

A `403 ... Permission denied` or `PERMISSION_DENIED` in the logs means a role is missing: check steps 2 and 3. A deploy error mentioning `iam.serviceaccounts.actAs` means step 4.

## Rolling back

Send traffic back to the previous revision, which still runs as the old account:

```
gcloud run revisions list --service=sitechat-api --region=us-central1 --limit=3
gcloud run services update-traffic sitechat-api --region=us-central1 --to-revisions=PREVIOUS_REVISION=100
```

## Cost

Nothing: service accounts and IAM bindings are free.

## Later, optional

The default compute account still has Editor. Nothing of ours runs as it after this, except perhaps Cloud Build (step 4). Removing Editor from it is a further step, but if builds run as it, they would then need their own roles (Cloud Run admin, Artifact Registry writer, service account user). Not worth doing until there is a reason.
