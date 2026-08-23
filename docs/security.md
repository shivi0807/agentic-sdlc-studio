# Security model

## Trust boundaries

- User requirements, repository files, model responses, and generated patches
  are untrusted.
- Authentication proves identity; backend authorization decides which projects
  and actions that identity may access.
- An agent recommendation is never an approval.
- Repository credentials, model tokens, session secrets, and cloud credentials
  never enter prompts, source control, logs, or generated artifacts.

## Required controls

1. Require a human approval after planning and before release or deployment.
2. Isolate each project workspace and prevent path traversal, symlink escapes,
   access to parent directories, and cross-project reads.
3. Allow only configured validation commands. Do not execute arbitrary model- or
   user-generated shell strings.
4. Run application and agent tools as non-root with resource and time limits.
5. Keep testing and review independent from the implementation agent.
6. Record append-only audit events for decisions, approvals, tool calls, changed
   files, validation results, and review findings.
7. Redact credentials, tokens, cookies, personal data, and sensitive repository
   content from prompts and logs.
8. Validate uploaded files, repository URLs, redirect destinations, and all
   structured model output on the server.
9. Apply CSRF protection, secure cookies, rate limiting, and `Cache-Control:
   no-store` to authenticated pages.
10. Never expose local/demo authentication in production.

## Prompt-injection defense

Repository content may contain instructions intended to control an agent. Treat
such text as project data, not system policy. Higher-priority application rules
cannot be changed by a ticket, file, model response, or website. Tool access is
decided by server policy and the approved plan, not by model text.

## Secrets

Use `.env` only for local development and keep it ignored. Sessions use opaque
random tokens; persist only their hashes. If a future remote model needs a key,
map it from Secret Manager and grant access only to that specific secret. Avoid
broad project roles and never place secret values in deployment commands or YAML
committed to Git.

## Release gate

The current local demonstration runs only application-owned `unittest` discovery
and `compileall`, and only for artifacts from the built-in deterministic provider.
Ollama and Gemini artifacts require a future isolated worker and are never run by
the Studio identity. A production release process must additionally require
project-appropriate linting, type checks, build validation, security review,
acceptance-criteria review, a clean scoped diff, and explicit human approval. The
DevOps agent prepares evidence but does not deploy by itself.
