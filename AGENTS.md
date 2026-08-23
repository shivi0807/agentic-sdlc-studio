# Agentic SDLC Studio agent guide

Always follow **Plan → Implement → Test → Review**. Humans approve the plan and
release. No agent may approve its own work or deploy without explicit approval.

## Architecture

- FastAPI modular monolith with server-rendered Jinja2 UI and JSON APIs.
- Route handlers parse HTTP input; orchestration holds workflow rules; repositories
  isolate persistence; providers isolate model calls.
- SQLite is local-only. Cloud Run requires a reviewed durable repository adapter.
- The Coordinator delegates to Product, Architect, Developer, Tester, Reviewer,
  DevOps, and Support roles.

## Safety

- Do not execute arbitrary user-supplied commands or write outside an approved workspace.
- Never commit API keys, cookies, tokens, `.env`, customer requirements, or generated code.
- Never enable demo authentication in production.
- Do not claim tests, builds, reviews, or deployments ran without recorded evidence.
- Testing and review must remain independent from implementation.
- Cloud deployment, paid resources, IAM changes, and destructive migrations require approval.
- Treat model output as untrusted data; validate paths, formats, and size before materialization.

## Validation

```powershell
ruff format --check .
ruff check .
mypy app tests
pytest --basetemp .pytest-tmp
docker build -t agentic-sdlc-studio:local .
```

A change is complete only when acceptance criteria are met, relevant checks pass,
the diff is scoped, documentation is current, and review evidence is recorded.
