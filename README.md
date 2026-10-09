# Issue-to-Patch

Turn a GitHub issue into a code-informed resolution plan, then investigate it and
review a proposed patch—all from a local web app.

The app reads the issue and repository, finds relevant code, and uses an AI model
to suggest an approach. A separate investigation can produce changes across one
or more files. You review the changes before choosing whether to build a branch,
push it to your fork, or open a pull request.

**A plan is a starting point. A validated patch is not proof that the bug is fixed.**
The app checks patch application and safety rules; you still need to review the
code and run the target project's relevant tests.

## Start here

- [Install and run](#install-and-run)
- [Use the web app](#use-the-web-app)
- [How it works](#how-it-works)
- [Configuration and API keys](#configuration-and-api-keys)
- [Troubleshooting](#troubleshooting)
- [Development commands](#development-commands)

## Install and run

### 1. Install the project

You need **Git**, **uv**, and **make**. The project uses **Python 3.12**;
`make install` installs that Python version through uv and installs dependencies.
The commands below assume a macOS or Linux shell.

```bash
git clone https://github.com/sarthakraghuvanshi/issue-to-patch-pipeline.git
cd issue-to-patch-pipeline
make install
```

Docker, Redis, and PostgreSQL are **not required** for the normal local setup.
It uses SQLite and files in the `artifacts/` directory.

### 2. Configure the AI models

For a new checkout, create your local settings file:

```bash
cp .env.example .env
```

If you already have a `.env`, edit that file instead of replacing it.
For OpenAI, set these values inside `.env`:

```dotenv
ITP_LLM_PROVIDER=openai
ITP_OPENAI_API_KEY=your-openai-api-key
ITP_LLM_MODEL=gpt-5
ITP_EMBEDDING_MODEL=text-embedding-3-large
```

The planning model writes the explanation and proposed changes. The embedding
model helps search the repository for relevant code. They are separate settings.
Real model calls require an API account with available credits or quota.

The example file starts with `ITP_LLM_PROVIDER=fake`. That is for automated tests,
not for generating real plans from arbitrary GitHub issues. See the
[offline demo](#try-the-offline-demo) if you want to explore without API calls.

Keep `.env` private; it is excluded from Git. Avoid setting both a generic
`ITP_LLM_API_KEY` and a provider-specific key unless you intend to override the
provider key for the planning model.

### 3. Create the database and start the server

```bash
mkdir -p artifacts
make migrate
make serve
```

Open **[http://127.0.0.1:8000/ui/runs](http://127.0.0.1:8000/ui/runs)**.
Keep the terminal running. Press **Ctrl+C** to stop the server.

Other useful local URLs:

| URL | Purpose |
| --- | --- |
| `/ui/runs` | Web app and investigation history |
| `/docs` | Interactive API documentation |
| `/openapi.json` | API contract |

Restart the server after changing `.env`. `make serve` reloads Python code during
development, but changes to `.env` do not reliably trigger a reload.

## Use the web app

### Browse issues and read a plan

1. Open the app and enter a GitHub repository URL in the repository browser.
2. Browse its open issues. Use **Previous** and **Next** for additional pages;
   pull requests are excluded.
3. Check the plan status beside the issue:

   | Status | What to do |
   | --- | --- |
   | **No plan yet** | Click **Show resolution plan** to generate one. |
   | **Plan in progress** | Click **View progress** to follow the existing job. |
   | **Plan ready** | Click **View plan** to read the saved result. |
   | **Plan failed / interrupted** | Open the status panel and use **Retry**. |

4. Read the problem summary, suggested steps, relevant code, verification ideas,
   and open questions. Click a code reference to see the saved source excerpt.

Plans are saved in the database. Refreshing the page or returning later restores
their status. **View plan does not generate another plan.** Use **Regenerate**
explicitly when you want a new answer against the same saved repository version.
A failed regeneration keeps the previous successful preview.

Generating a plan does not edit code, create a branch, push changes, or open a PR.
The first plan for a large repository can take several minutes because the code
must be downloaded and indexed. Compatible indexes are reused for the same commit.

### Investigate and review a fix

1. Choose **Start investigation** from a ready plan, or use the issue's run action.
2. The investigation examines evidence and proposes a patch. A plan is treated as
   provisional guidance, not an established diagnosis.
3. Review the explanation and the current/modified code comparison for each changed
   file. A patch can change multiple files when the proposed fix requires it.
4. Review the validation results and approve or reject the proposed changes.
5. For an approved, validated patch, optionally **Build branch**, **Push branch**
   to your fork, and **Create Pull Request**.

Each publishing action is separate. Approval alone does not push changes to GitHub.
Git push needs working Git credentials; opening a PR needs a GitHub token with
appropriate access. The UI/API support creating PRs; the CLI `auto` workflow ends
with its optional push step.

## How it works

| Step | What the application does |
| --- | --- |
| **Fetch** | Reads the GitHub issue and discussion. |
| **Snapshot** | Downloads a repository copy pinned to a specific commit. |
| **Index** | Splits supported source files into searchable pieces called chunks. |
| **Retrieve** | Combines keyword search with embedding similarity to select relevant code. |
| **Plan** | Asks the configured model for a structured, cited starting point. |
| **Investigate** | Examines evidence, proposes edits, and creates a patch in an isolated working copy. |
| **Validate and review** | Checks the patch, shows the diff, and waits for a human decision. |
| **Publish, if requested** | Builds a local branch, pushes to your fork, and can open a PR. |

A **snapshot** is the exact repository version used for the work. Starting an
investigation from a plan reuses that snapshot and index, so the code does not
silently change between planning and investigation.

An **embedding** is a numeric representation used to find related code.
Oversized inputs are split before being sent to the embedding provider; the full
source text remains stored. Changing between the small and large models triggers
a rebuild when the saved vector dimensions no longer match.

An investigation can end with:

| Result | Meaning |
| --- | --- |
| `PATCH_VALIDATED` | The patch passed the implemented checks and required approval. |
| `PATCH_REQUIRES_HUMAN_REVIEW` | The result still requires human attention. |
| `PATCH_REJECTED` | The patch failed a required check or was rejected. |
| `INVESTIGATION_INCONCLUSIVE` | The investigation could not establish a usable fix. |

Patch checks include whether the patch applies, stays within the allowed file
scope, and avoids detected secrets or unsupported binary changes. The standard
workflow does **not** automatically run the target repository's full test suite.
Verification steps in a resolution plan are suggestions, not executed tests.

## Configuration and API keys

Settings come from `.env` or environment variables prefixed with `ITP_`.
Environment variables override `.env`. See [.env.example](.env.example) for options.

| Setting | Purpose |
| --- | --- |
| `ITP_LLM_PROVIDER` | `openai`, `anthropic`, or `fake`. |
| `ITP_OPENAI_API_KEY` | OpenAI access for planning and embeddings. |
| `ITP_ANTHROPIC_API_KEY` | Anthropic access when using that planning provider. |
| `ITP_LLM_API_KEY` | Optional generic planning key; takes precedence over the provider-specific planning key. |
| `ITP_LLM_MODEL` | Model that writes plans and reasons about fixes. |
| `ITP_EMBEDDING_MODEL` | Repository search model. The example configuration uses `text-embedding-3-large`. |
| `ITP_GITHUB_TOKEN` | GitHub API access, including PR creation. Public issue browsing can work without it, subject to GitHub limits. |
| `ITP_API_KEY` | A key **you choose** to protect this application's API. It is not an OpenAI or GitHub key. Optional for local use; required in staging/production. |
| `ITP_PUSH_REMOTE_URL` | Your fork's Git remote, used when pushing a generated branch. |
| `ITP_AGENT_MODE` | `single` by default; `multi` enables the specialist-agent investigation workflow. |
| `ITP_DATABASE_URL` | Defaults to `sqlite+pysqlite:///./artifacts/dev.db`. |
| `ITP_ARTIFACTS_DIR` | Directory for generated files; defaults to `artifacts`. Changing it does not automatically change the database URL. |

If `ITP_API_KEY` is set, enter that same value in the UI's workspace API-key field.
API clients send it as `Authorization: Bearer <key>`. Plans are scoped to the current
application identity; separate registered users do not share private plan previews.
Private repository cloning also requires working Git credentials on the server.

## Saved data and restarts

With the default configuration:

- `artifacts/dev.db` stores plans, indexed code, run records, and application users.
- `artifacts/checkpoints.db` stores investigation graph checkpoints.
- `artifacts/issue-plans/` holds planning snapshots.
- Other directories under `artifacts/` hold run snapshots, patches, and built branches.

Keep both the database and the snapshot files if you want to continue an old plan.
Deleting a snapshot prevents investigation handoff from that plan.

Use **one API worker**. Planning runs in the server process, not a separate durable
job queue. Completed plans survive restarts; unfinished plans are marked interrupted
and can be retried. `make serve` is a development server, not a production deployment.

To completely reset **default local storage**, stop the server and run these commands
from the project root. This deletes local plans, runs, users, snapshots, indexes,
checkpoints, and patches. It keeps source code and `.env`, and does not change GitHub.

```bash
rm -rf -- artifacts
mkdir -p artifacts
make migrate
make serve
```

Custom database URLs or artifact directories need their own reset procedure.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Plan fails with missing API credits or quota | Resolve billing/quota for the configured API account, then click **Retry**. Changing models does not provide credits. |
| FakeLLM reports no queued response | Set a real `ITP_LLM_PROVIDER` and its API key for live planning. FakeLLM is a test stub. |
| Plan takes several minutes | The first request may be cloning and embedding a large repository. Open **View progress**; repeated generation is unnecessary. |
| Plan says interrupted | Restarting the server stops its background jobs. Use **Retry**. |
| Unauthorized / HTTP 401 | Check the workspace API key. It must match the server's `ITP_API_KEY` or a valid registered user's key. |
| Repository not found or clone fails | Check the URL, repository access, and server Git credentials. |
| Embedding dimensions changed | The saved embeddings must be rebuilt. Keep available provider quota and allow the refresh to finish. |
| Missing table after an update | Stop the server, run `make migrate`, then restart. Back up existing data before resetting it. |

The development terminal shows server logs. Failure messages identify the stage;
server logs include the plan ID and error type. Opening an existing plan does not
need a new model call.

## Try the offline demo

```bash
make demo
```

This creates a temporary calculator repository with a bug and applies a predefined
edit plan. It demonstrates patch creation and validation without calling an LLM.
It does not demonstrate live AI diagnosis.

## Development commands

| Command | Purpose |
| --- | --- |
| `make install` | Install Python and dependencies. |
| `make migrate` | Apply database migrations. |
| `make serve` | Start the local web app. |
| `make check` | Run lint, type checks, and tests with coverage. |
| `uv run ruff format --check .` | Check formatting, as CI does. |
| `make openapi` | Regenerate the committed API contract after changing endpoints. |
| `make eval` | Run retrieval evaluation using the labeled dataset. |
| `make up` / `make down` | Start/stop optional Docker development services. |

For the interactive command-line workflow:

```bash
uv run issue-to-patch auto https://github.com/OWNER/REPO/issues/123
```

Replace the example URL with a real issue. Use `uv run issue-to-patch --help` for
individual ingestion, indexing, search, investigation, and audit commands.

## Code and further reading

| Directory | Responsibility |
| --- | --- |
| `src/issue_to_patch/api/` | Web UI and HTTP endpoints. |
| `src/issue_to_patch/ingestion/` | GitHub data and repository snapshots. |
| `src/issue_to_patch/processing/` | Parsing, chunking, and indexing. |
| `src/issue_to_patch/retrieval/` | Keyword and embedding search. |
| `src/issue_to_patch/planning/` | Saved, read-only resolution plans. |
| `src/issue_to_patch/graph/` and `agents/` | Investigation flow and reasoning. |
| `src/issue_to_patch/patching/` | Patch creation, checks, branches, and pushing. |
| `src/issue_to_patch/persistence/` | Stored data and migrations. |
| `tests/` | Automated tests using fake model responses. |

- [Project guide in simple language](docs/project-guide.md)
- [Resolution-plan workflow and API](docs/issue-plans.md)
- [Implementation roadmap](IMPLEMENTATION_PLAN.md)
- [Learning notes](RAG_LEARNING_PLAN.md)
- [Evaluation dataset and results](evals/README.md)
