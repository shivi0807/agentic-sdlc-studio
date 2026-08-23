# Git workflow

The Studio repository uses a simple, reviewable branching model.

| Branch | Purpose |
|---|---|
| `main` | reviewed, stable Studio application |
| `feature/<topic>` | an application feature under review |
| `project/<slug>-<id>` | a generated project implementation workspace |

Each project workspace receives an isolated local Git branch. The branch name
uses a safe project slug plus a short immutable project identifier, so two
projects with the same title cannot overwrite one another.

Never commit `.env`, API keys, session data, customer requirements, generated
source, SQLite databases, or local workspaces to the Studio application repo.
Project source should be reviewed and explicitly promoted from its isolated
workspace only after the human release gate.

Before a pull request, run the validation commands in `AGENTS.md`. The pull
request description must contain the requirement, implementation summary,
validation evidence, review outcome, and rollback notes.
