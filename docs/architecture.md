# Architecture

## Purpose

Agentic SDLC Studio coordinates specialist software-development agents while a
human retains control over scope, implementation, and release decisions.

## Logical components

```text
Browser UI
   |
FastAPI application
   |-- Authentication and role checks
   |-- Project and requirement services
   |-- SDLC workflow state machine
   |-- Human approval service
   |-- Agent coordinator
   |     |-- Product and planning agent
   |     |-- Architecture agent
   |     |-- Development agent(s)
   |     |-- Testing agent
   |     |-- Review agent
   |     |-- DevOps agent
   |     `-- Support agent
   |-- Provider abstraction (deterministic, Ollama, or Gemini)
   |-- Repository abstraction (SQLite locally; cloud adapter later)
   `-- Git workspace adapter
```

The coordinator delegates bounded tasks. Specialist agents exchange structured
artifacts through the application rather than unrestricted direct messages.
Every transition is recorded as an audit event.

## User-facing lifecycle

The UI always exposes four simple stages:

1. **Plan** — clarify requirements, select the SDLC method, design the solution,
   and obtain human approval.
2. **Implement** — create controlled workspaces and change only approved scope.
3. **Test** — run the project's allow-listed validation commands; failures return
   to implementation.
4. **Review** — independently assess acceptance criteria, security, quality, and
   release evidence before human approval.

Agile repeats these stages for each sprint. Waterfall completes formal phase
gates in sequence. Hybrid uses an approved high-level design with iterative
implementation.

## Persistence and workspaces

SQLite is appropriate for a single-user local demonstration. It is not durable
inside an ephemeral Cloud Run filesystem. A production cloud version must use a
separately implemented repository adapter backed by a durable service, and Git
workspaces must use approved external storage or a remote Git provider.

Do not present ephemeral Cloud Run disk as persistent storage. Firestore may fit
the repository abstraction and [offers a limited free quota](https://cloud.google.com/firestore/pricing),
but quotas and pricing must be verified at deployment time and zero cost is not
guaranteed.

## Model providers

`AgentProvider` isolates orchestration from inference:

- `OllamaAgentProvider`: local development and private zero-API-fee execution.
- `GeminiAgentProvider`: optional Google free-tier execution using a server-side
  API key. Quotas and data-handling terms must be reviewed before use.
- `DeterministicAgentProvider`: optional test fixtures with no network calls.

Provider output is untrusted input. The coordinator validates structured output,
applies policy, and requires approval before any side effect.

The provider choices are `deterministic`, `ollama`, and `gemini`. The
deterministic provider is useful for tests and UI demonstrations; it is not
generative AI.

Trust is explicit: only the built-in deterministic provider is marked safe for
in-process local execution. The Studio may store bounded Ollama or Gemini
artifacts, but it does not execute their code. Their Test task is blocked with
`isolated_worker_required` until an isolated, resource-limited validation worker
is implemented. The current trusted local recipe is limited to `unittest`
discovery and `compileall`.

## Deployment shape

The lowest-idle-cost Google Cloud design is one Cloud Run service with minimum
instances set to zero and maximum instances capped. It should use a durable data
adapter and remote model endpoint only after those integrations are reviewed.
Cloud-hosting an LLM is intentionally outside the zero-cost baseline.
