# Google Cloud deployment guide

This is a production-readiness plan. It does not by itself authorize deployment,
billing changes, IAM changes, or resource creation.

## Cost statement

Cloud Run's request-based billing can scale to zero and includes a monthly free
usage allowance in eligible billing accounts and regions. That makes it suitable
for a small demonstration, but **Google Cloud does not guarantee a zero bill**.
Free-tier eligibility and prices can change. Artifact Registry storage, Cloud
Build, networking, logs, databases, Secret Manager, and model inference may all
incur charges.

Before deployment:

- Read the current [Cloud Run pricing](https://cloud.google.com/run/pricing) and
  [Google Cloud Free Program](https://cloud.google.com/free/docs/free-cloud-features).
- Use a dedicated project linked to a billing account only after human approval.
- Create [budget alerts](https://cloud.google.com/billing/docs/how-to/budgets) at
  a very small threshold. Alerts notify; they do not cap or stop spending.
- Select an eligible region deliberately and re-check free-tier conditions.
- Keep minimum instances at `0`, maximum instances at `1`, and conservative CPU,
  memory, concurrency, timeout, and logging settings.

## Authentication and public access

Public visitors may view only explicitly shared project output. Project creation,
agent execution, approvals, and support actions require a verified Google sign-in.
The application supports `AUTH_MODE=google` with a Google OAuth **Web application**
client. Configure its authorized redirect URI to:

```text
https://YOUR-CLOUD-RUN-DOMAIN/auth/google/callback
```

Store `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and
`GEMINI_API_KEY` only in Secret Manager. Never put them in GitHub, an image,
or a plain Cloud Run environment variable. Production startup refuses local
demo authentication and requires Google sign-in configuration.

## Durable cloud storage

The local SQLite database and local Git workspaces are not durable on Cloud Run.
The container filesystem is ephemeral and may disappear whenever an instance is
replaced, as documented in the
[Cloud Run runtime overview](https://cloud.google.com/run/docs/overview/what-is-cloud-run).
The application selects Firestore for users, sessions, projects, runs, audit
events, approvals, defects, and usage records when
`PERSISTENCE_BACKEND=firestore`. It selects the private Cloud Storage workspace
adapter when `WORKSPACE_BACKEND=gcs`. Cloud Run uses Application Default
Credentials from its runtime service account; no service-account key is needed.

Externally generated code is still never executed inside the web process. A
Gemini run can plan and implement, but its Test stage deliberately reports
`isolated_worker_required` until a separately reviewed, isolated validation
worker exists. Do not describe a Gemini run as a complete tested release before
that boundary is implemented.

Cloud Run cannot reach Ollama running at `localhost` on a developer laptop.
Inside Cloud Run, `localhost` is the container. Hosting Ollama or another LLM in
Google Cloud generally requires continuously billed compute and often a GPU, so
it is not part of the zero-cost target.

For a limited demonstration, use deterministic mode or the implemented Gemini
Developer API provider with prepaid credit and strict usage monitoring.
Google's free tier can change without notice, and free-tier prompts may be used
to improve Google products. Do not submit confidential requirements. Hosting an
open model on Google Cloud is not treated as a zero-cost design.

## Minimal deployment shape

```text
Public HTTPS
    |
Cloud Run (min 0, max 1, non-root container)
    |-- Secret Manager references
    |-- Google OAuth sign-in for creators
    |-- Firestore and private Cloud Storage adapters
    `-- Gemini Developer API (optional) or deterministic provider
```

Use a dedicated least-privilege service account. Do not use owner/editor roles
for the runtime. If user login is implemented by the application, public ingress
may be allowed while backend authorization remains mandatory for every private
route.

## Template workflow

The script in `deploy/google-cloud/deploy-cloud-run.ps1` expects an already-built
container image. It intentionally does not create projects, billing accounts,
IAM policies, databases, authentication configuration, or secrets. It requires
an explicit readiness switch and confirmation. The deployment references the
existing `gemini-api-key`, `google-oauth-client-id`, and
`google-oauth-client-secret` secrets without printing their payloads.

After human approval and manual prerequisites:

```powershell
.\deploy\google-cloud\deploy-cloud-run.ps1 `
  -ProjectId "YOUR_PROJECT_ID" `
  -Region "us-central1" `
  -Image "us-central1-docker.pkg.dev/YOUR_PROJECT/agentic-sdlc/app:TAG" `
  -RuntimeServiceAccount "agentic-sdlc-runtime@YOUR_PROJECT.iam.gserviceaccount.com" `
  -WorkspaceBucket "YOUR_PRIVATE_BUCKET" `
  -OAuthRedirectUri "https://YOUR-CLOUD-RUN-DOMAIN/auth/google/callback" `
  -ProductionReadinessApproved
```

Review the generated `gcloud` command shown by PowerShell. The script asks for an
explicit confirmation before running it. Deployment is still a potentially
billable action.

## Post-deployment checks

1. Verify HTTPS, health, login, authorization, CSRF, and secure cookies.
2. Confirm no demo credentials or local authentication are enabled.
3. Verify secrets and user input do not appear in logs.
4. Confirm min/max instance and resource limits.
5. Test restart behavior and durable persistence.
6. Run a small approved requirement through Plan → Implement → Test → Review.
7. Inspect billing and logs, then disable or delete the service when the demo is
   finished. Deletion requires separate human approval.
