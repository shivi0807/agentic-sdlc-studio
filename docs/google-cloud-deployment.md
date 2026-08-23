# Google Cloud deployment guide

This is an optional future deployment plan. It does not authorize deployment,
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

## Blocking architecture decisions

The local SQLite database and local Git workspaces are not durable on Cloud Run.
The container filesystem is ephemeral and may disappear whenever an instance is
replaced, as documented in the
[Cloud Run runtime overview](https://cloud.google.com/run/docs/overview/what-is-cloud-run).
Implement and test durable repository/workspace adapters before calling this a
production deployment.

Cloud Run cannot reach Ollama running at `localhost` on a developer laptop.
Inside Cloud Run, `localhost` is the container. Hosting Ollama or another LLM in
Google Cloud generally requires continuously billed compute and often a GPU, so
it is not part of the zero-cost target.

For a limited demonstration, use deterministic mode or the implemented Gemini
Developer API provider with a free-tier API key and strict usage monitoring.
Google's free tier can change without notice, and free-tier prompts may be used
to improve Google products. Do not submit confidential requirements. Hosting an
open model on Google Cloud is not treated as a zero-cost design.

## Minimal deployment shape

```text
Public HTTPS
    |
Cloud Run (min 0, max 1, non-root container)
    |-- Secret Manager references
    |-- durable repository adapter (required)
    `-- Gemini free-tier API (optional) or deterministic provider
```

Use a dedicated least-privilege service account. Do not use owner/editor roles
for the runtime. If user login is implemented by the application, public ingress
may be allowed while backend authorization remains mandatory for every private
route.

## Template workflow

The script in `deploy/google-cloud/deploy-cloud-run.ps1` expects an already-built
container image. It intentionally does not create projects, billing accounts,
IAM policies, databases, authentication configuration, or secrets. It is blocked
by default until production authentication and durable storage are implemented.

After human approval and manual prerequisites:

```powershell
.\deploy\google-cloud\deploy-cloud-run.ps1 `
  -ProjectId "YOUR_PROJECT_ID" `
  -Region "us-central1" `
  -Image "us-central1-docker.pkg.dev/YOUR_PROJECT/agentic-sdlc/app:TAG" `
  -RuntimeServiceAccount "agentic-sdlc-runtime@YOUR_PROJECT.iam.gserviceaccount.com" `
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
