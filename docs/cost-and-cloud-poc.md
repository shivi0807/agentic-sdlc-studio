# Cost controls and Google Cloud POC

## Local usage widget

The project dashboard records provider-reported input and output tokens for
each completed agent task. Deterministic mode is zero tokens and zero dollars.
Ollama records its local token counters and reports zero API cost. Gemini usage
is shown when the provider returns metadata; its cost is deliberately marked
`Not priced` until a reviewed pricing configuration is supplied.

The dashboard is an estimate and a visibility control, not a billing system.
Do not treat a displayed value as a provider invoice or a hard spend limit.

## $5–$10 proof of concept

Use a separate Google Cloud project and set a small test budget. Keep Cloud Run
request-based, scale-to-zero, single-region, and low traffic. Use a low-cost
model with a strict application-side limit on agent runs and request size.
Disable or delete the POC after testing.

Google's new-customer trial credit is normally $300 for 90 days when eligible.
Cloud Run has a free usage allowance, but billing, network egress, logging,
storage, and model usage can still create charges. The Google AI Studio Gemini
API is not paid with the $300 Google Cloud trial credit. Confirm current pricing
and model availability immediately before deployment.

## Before deployment

Cloud deployment remains blocked until these production changes are reviewed:

1. Replace demo authentication with a production identity provider.
2. Replace SQLite and local workspaces with durable, isolated cloud adapters.
3. Add a sandbox worker for untrusted model-generated code.
4. Create billing budget alerts and least-privilege service accounts.
5. Approve the actual Cloud Run, Firestore, model, and IAM configuration.

No cloud resource, billing account, service account, or API key is created by
this repository.
