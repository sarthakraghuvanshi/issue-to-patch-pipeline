# How Issue-to-Patch works: a beginner’s guide

This project helps you turn a GitHub bug report into a proposed code fix. You give it an issue, it looks for relevant code, asks an AI model to investigate, prepares changes, and lets you review them.

Think of it as a junior developer with a checklist. It can investigate and suggest a fix, but its suggestion still needs checking. It does not automatically mean the bug is solved.

This guide describes the implementation in this repository. The calculator examples below are imaginary examples to explain the process, not results from an actual investigation.

## Contents

1. [The basic idea](#1-the-basic-idea)
2. [What happens when you click Start](#2-what-happens-when-you-click-start)
3. [One small example](#3-one-small-example)
4. [How the project finds the right code](#4-how-the-project-finds-the-right-code)
5. [How LangGraph controls the work](#5-how-langgraph-controls-the-work)
6. [How code changes are created and checked](#6-how-code-changes-are-created-and-checked)
7. [What you see in the interface](#7-what-you-see-in-the-interface)
8. [What approval actually does](#8-what-approval-actually-does)
9. [Where the information is saved](#9-where-the-information-is-saved)
10. [The AI models and specialist agents](#10-the-ai-models-and-specialist-agents)
11. [How the code is organized](#11-how-the-code-is-organized)
12. [Running the project and understanding keys](#12-running-the-project-and-understanding-keys)
13. [Tests, limitations, and common confusion](#13-tests-limitations-and-common-confusion)
14. [A small glossary and reading order](#14-a-small-glossary-and-reading-order)

## 1. The basic idea

A **repository** is a project’s collection of code files. A **GitHub issue** is a report or discussion about that project, such as “clicking Save causes an error.” A **patch** is a description of the exact lines to add and remove in the code.

The main input is a GitHub issue URL. The useful outputs are:

- A possible explanation of the problem, called a **diagnosis** or **hypothesis**.
- References to relevant code, called **citations**.
- A proposed patch, potentially changing several files.
- A report showing the checks performed on that patch.
- A recorded outcome after review.

One attempt is called a **run** or **investigation**. Its **run ID** is a tracking number that connects the issue, evidence, patch, and review decision.

The main journey is:

```text
GitHub issue
    ↓
Download issue information and a copy of the repository
    ↓
Organize code so it can be searched
    ↓
Find relevant code and ask AI to explain the problem
    ↓
Create a proposed patch
    ↓
Run programmed checks
    ↓
Ask a person to review
    ↓
Save the outcome and available patch/report
```

The real workflow can go back and try again. For example, weak evidence can trigger another search, and a failed patch can trigger a revision.

## 2. What happens when you click Start

### A. The browser sends your request

The page collects your GitHub issue URL and optional file scope. JavaScript sends them to `POST /runs/auto`.

An **API** is a set of operations one program exposes to another. Here, the browser is asking the Python server: “Please start an investigation using this information.” `POST` means sending information to perform an action.

The browser is the presentation layer. The Python server does the investigation. This is why changing the page’s appearance does not change the AI’s reasoning.

### B. The server fetches the issue and repository

The server creates an ID and an output directory. It fetches issue information from GitHub and creates a repository **snapshot**: a copy tied to a particular Git commit.

A commit identifies a particular version of the code. Pinning the snapshot means the investigation has a stable starting point even if somebody updates the original repository later.

The ingestion code also handles supporting GitHub information, such as comments, and saves raw information for later inspection.

### C. It makes the snapshot searchable

The server divides supported source files into smaller pieces and saves them in an index. An **index** is organized information that makes searching easier, like the index at the back of a book.

### D. It runs the investigation workflow

The workflow plans searches, gathers evidence, suggests a cause, and attempts to create a patch. The next chapters explain these steps.

### E. It responds right away, and does the work in the background

`POST /runs/auto` hands the real work (fetch, index, investigate) to a background task and responds immediately with just the new run's ID — it does not make the browser wait for any of it. The browser navigates to the run's review page right away.

That review page does not yet have a real investigation behind it the moment you land on it — fetching and indexing can themselves take tens of seconds. Until the first real result exists, the page shows a short "investigating…" message and refreshes itself automatically every few seconds, with no need to reload by hand. Once the investigation reaches a real diagnosis (or fails), the page simply shows that instead.

**Follow this in code:** [browser actions](../src/issue_to_patch/api/ui.js), [API routes](../src/issue_to_patch/api/routes.py), [shared automatic workflow](../src/issue_to_patch/auto_run.py), and [the pending-run tracker](../src/issue_to_patch/api/pending_runs.py) that the review page checks while nothing has started yet.

## 3. One small example

Imagine a repository contains this function:

```python
def add(a, b):
    return a - b
```

Someone reports: “`add(5, 3)` returns 2, but it should return 8.”

The investigation might work like this:

1. Read the report and understand the expected result.
2. Search for code related to `add` and calculation.
3. Find the function shown above.
4. Suggest: “This function subtracts instead of adding.”
5. Propose replacing `return a - b` with `return a + b`.
6. Check whether the replacement can be applied to the saved repository version and stays within the allowed files.
7. Show you the proposed change for review.

You would see something like:

| Current version | Modified version |
| --- | --- |
| `return a - b` | `return a + b` |

The diagnosis is an explanation. The patch is the actual proposed edit. Validation is a set of checks on the patch. These are three different outputs.

A real fix might need changes in both the source file and a test file. The edit plan can contain multiple edits, and the review page gives each changed file its own comparison.

## 4. How the project finds the right code

### Why it splits code into chunks

A repository may contain thousands of files. Giving every line to an AI model in every request would be slow, expensive, and often too large for the model to process.

Instead, the project creates **chunks**: smaller pieces of source code. Where supported, parsing helps identify useful boundaries such as functions or classes. Chunks retain information about their file and line locations.

For example:

```text
Chunk content: the add() function
File: calculator.py
Location: lines 1–2
Chunk ID: an identifier used to refer back to this piece
```

This extra information is called **metadata**. It makes a search result traceable to real code.

### It uses more than one kind of search

| Search term | Simple explanation | What this project does |
| --- | --- | --- |
| BM25 | Rank text using matching words | Find chunks matching the query’s terms |
| Vector search | Compare lists of numbers representing text | Compare query and chunk representations |
| Hybrid search | Combine multiple search rankings | Combine keyword and vector results |
| Rank fusion | Combine positions in ranked lists | Reward chunks that rank well across searches |

An **embedding** is a numerical representation of text. The default implementation here uses a local `HashingEmbedder`. It is a deterministic token-based representation, not a paid neural embedding service that deeply understands meaning. A configuration field naming an embedding model does not mean that model is used by the default search implementation.

### What RAG means here

**RAG** stands for retrieval-augmented generation. In ordinary language:

> Find relevant information first, then give that information to the AI when asking it to answer.

The project retrieves code, then gives relevant evidence to the model to help it explain the issue and draft changes. This does not train a new AI model on your repository.

If search results are weak, or the diagnosis is uncertain, the workflow can expand the search. It does not keep searching forever.

**Follow this in code:** [indexing](../src/issue_to_patch/processing/index.py), [search service](../src/issue_to_patch/retrieval/service.py), and [rank fusion](../src/issue_to_patch/retrieval/hybrid.py).

## 5. How LangGraph controls the work

LangGraph helps organize a process made of steps, choices, and pauses.

Think of a paper checklist with arrows. Each step is a **node**. An arrow says what to do next. Some arrows depend on whether a check succeeded.

The project defines these nodes:

| Node | What it means |
| --- | --- |
| `NormalizeRequest` | Prepare the issue information in the expected format |
| `PlanInvestigation` | Decide what to search for |
| `RetrieveIssueContext` | Collect the issue-related evidence |
| `RetrieveCodeContext` | Find relevant source code |
| `AnalyzeRootCause` | Suggest why the problem happens |
| `SelectAdditionalEvidence` | Search more broadly when needed |
| `DraftPatch` | Attempt to produce a code change |
| `RunPatchValidation` | Check the proposed patch |
| `RequestHumanValidation` | Process a person’s review decision; the graph pauses before this node |
| `RevisePatch` | Try an adjusted patch |
| `EvaluateRun` | Summarize aspects of the investigation |
| `PersistRun` | Save the final outcome and available artifacts |

### State is the investigation’s shared notebook

The **state** contains what the workflow knows so far: the issue, repository, plan, evidence, hypotheses, current patch, validation results, revision count, human decision, and errors.

A node reads that notebook and returns updates. Evidence and errors accumulate; values such as the current patch are replaced with the latest version.

### The router decides the next step

The router is ordinary Python logic. The AI proposes explanations and edits; it does not have unrestricted control over the workflow.

Examples of routing rules:

- Weak search results can trigger an additional search.
- A diagnosis that remains too weak can end as inconclusive.
- A patch that cannot be created or fails checks can be revised if budget remains.
- A patch that passes checks still reaches the human-review gate.
- An explicit rejection ends the run.

The default revision budget is one revision. A request to revise does not grant unlimited attempts.

### A checkpoint is a saved place in the workflow

When the graph pauses for review, its saved state lets another request continue it later. The API uses a SQLite checkpoint file, so this can survive a server restart when the files remain available.

The graph’s default checkpointer outside this API wiring is process-local. Do not assume every standalone command has the same restart behavior as the API.

**Follow this in code:** [graph assembly](../src/issue_to_patch/graph/build.py), [state](../src/issue_to_patch/graph/state.py), [routing](../src/issue_to_patch/graph/router.py), and [starting/resuming runs](../src/issue_to_patch/graph/run.py).

## 6. How code changes are created and checked

### The AI proposes an edit plan

An edit plan identifies a file, the existing text, and its replacement. For example:

```json
{
  "message": "Fix addition",
  "edits": [
    {
      "path": "calculator.py",
      "old": "return a - b",
      "new": "return a + b"
    }
  ]
}
```

The old text must match exactly once. This helps prevent an ambiguous replacement from silently changing the wrong place. Creating a new file has its own supported case: empty old text with a missing target file.

The project applies edits in a separate Git worktree, a separate working directory associated with the repository. Git then produces a patch describing the changes. Proposing a patch is not the same as modifying the original remote repository.

### Validation uses programmed checks

The validation code does not ask the model whether its own patch is good. It runs checks such as:

| Check | What it establishes |
| --- | --- |
| Patch syntax | The patch is nonempty and contains the expected diff marker; this is not a source-code compiler check |
| Applies to snapshot | Git can apply the patch to the recorded base commit |
| Scope | Changed files match the allowed file patterns |
| No binaries | The patch does not contain binary changes |
| No detected secrets | Added lines do not match the implemented secret patterns |
| Minimality | The patch is not unusually large under the configured code thresholds |

More than five changed files or more than 150 added/removed lines produces a size warning. It does not mean multiple-file patches are unsupported.

No scope produces a warning because the validator cannot confirm the requested boundaries. A pattern such as `src/**` means files under `src`; if the fix also needs `tests/**`, that needs to be included in the allowed scope.

**Passing these checks does not prove the bug is fixed or that the target repository’s tests passed.** The secret check is pattern matching, not a guarantee that every possible secret is detected.

**Follow this in code:** [worktree and edits](../src/issue_to_patch/patching/worktree.py) and [validation](../src/issue_to_patch/patching/validate.py).

## 7. What you see in the interface

The investigation form starts the workflow. Advanced settings contain the optional file scope and workspace access key.

The history screen shows saved runs, statuses, and recorded costs. Costs are recorded accounting information, not a live billing dashboard or a guarantee of the full provider bill.

The review screen shows the diagnosis, code references, patch, available validation results, and review controls when a decision is pending.

### Reading the side-by-side diff

- **Left: Current version.** Code from the investigation’s base snapshot.
- **Right: Modified version.** Code after the proposed change.
- **Red:** Removed or replaced code.
- **Green:** Added or replacement code.
- **Darker highlight:** Changed text within a line.
- **Blank patterned cell:** No corresponding line exists on that side.

“Current” means the saved starting version, not a fresh download of whatever happens to be on GitHub now.

The comparison shows the sections included in the patch, with surrounding context. It does not load every unchanged line of the whole file. A line starting with `@@` identifies the old and new line ranges for a changed section.

Each changed file gets a navigation link, its own comparison, counts, and a collapsible section. If only one file appears, the parsed patch has one file section; this does not mean the whole repository contains only one relevant file.

The UI renders the patch it receives. Changing the diff layout does not change the patch itself.

**Follow this in code:** [pages](../src/issue_to_patch/api/ui.py) and [diff renderer](../src/issue_to_patch/api/diff_render.py).

## 8. What approval actually does

The review form sends a decision to `POST /runs/{run_id}/approve`. Despite the endpoint name, the decision can be `approve`, `reject`, or `revise`.

| Decision | What happens |
| --- | --- |
| Approve | Resume the workflow and finish according to the authorization rules |
| Reject | Finish with a rejected result |
| Revise | Try another revision if budget remains; otherwise finish requiring review |

Risky paths require the Gatekeeper role to clear approval. Auditor and Strategist roles do not have the same authority for risky changes.

With a real per-user API key, the server uses the account’s stored identity and role. With the legacy shared-key or local fallback, the review information follows the older form-supplied behavior. Selecting a role in the form is not the same as establishing a verified identity.

### Understanding statuses

| Status | Plain-language meaning |
| --- | --- |
| `AWAITING_HUMAN_REVIEW` | The workflow is paused, waiting for a decision |
| `PATCH_VALIDATED` | The run completed with an authorized approval through this workflow; this is not proof of target test success |
| `PATCH_REQUIRES_HUMAN_REVIEW` | The run ended without clearing the required review, for example insufficient authority or no revision budget |
| `PATCH_REJECTED` | The patch failed relevant checks after retries, or a person rejected it |
| `INVESTIGATION_INCONCLUSIVE` | The investigation did not reach a supported, approved patch outcome |

The validator also uses some of these names for its intermediate check result. A passing validation report and a finished, human-approved run are different milestones.

### Approve, build, push, and pull request are separate

1. **Approve:** Record your decision about the proposal.
2. **Build/materialize:** Apply the approved patch onto a persistent local branch/worktree. Here “build” does not automatically mean compile the target application or run all its tests.
3. **Push:** Upload that branch to the configured remote through a separate action.
4. **Pull request:** Ask maintainers to merge a branch. The push workflow does not automatically create one.

The CLI `auto` workflow can offer separate build and push steps. The API also exposes build and push operations. The review page’s approval action itself does not automatically push code.

## 9. Where the information is saved

There are three different kinds of storage:

| Storage | Why it exists |
| --- | --- |
| Application database | Store runs, indexed chunks, artifact references, accounts, and audit-related records |
| Graph checkpoint database | Remember the workflow state so it can be inspected or resumed |
| Files under the artifacts directory | Keep repository snapshots, downloaded information, and output files |

The default application database is `artifacts/dev.db`. The API checkpoint file is `artifacts/checkpoints.db`. Settings can change locations.

An automatic run may create files arranged like this:

```text
artifacts/
  dev.db
  checkpoints.db
  <run_id>/
    raw/                 downloaded GitHub information
    snapshot/            repository copy and snapshot information
    chunks.jsonl         indexed pieces, one JSON record per line
    fix.patch            patch written when the graph persists its result
    validation.json      available validation report written at persistence
```

Not every file exists at every stage. A run still paused for review can have its candidate patch in checkpoint state before the final persistence step writes `fix.patch`.

An **artifact** is simply a file produced or saved by a run. The database can store its location and a content fingerprint rather than putting the entire file into a table.

The audit functionality records relevant activity and human decisions in a hash-linked trail. This helps detect changes to recorded history; it is not proof that the proposed fix is correct.

## 10. The AI models and specialist agents

An **LLM**, or large language model, is the AI used for tasks such as planning searches, explaining the likely cause, and drafting an edit plan.

The project supports a fake provider for controlled offline work and real Anthropic/OpenAI providers. The fake provider is useful for tests; it is not a real investigator for arbitrary GitHub issues.

### Single mode

The default agent mode uses a simpler analysis/drafting path. “Single” does not mean the whole investigation uses exactly one AI request.

### Multi mode

Setting `ITP_AGENT_MODE=multi` uses a sequence of specialized responsibilities:

| Specialist | Responsibility | Implementation |
| --- | --- | --- |
| Issue Analyst | Extract symptoms and expected behavior | AI call |
| Repository Cartographer | Organize retrieved files, symbols, and test paths | Programmed logic |
| Root-Cause Analyst | Suggest causes based on evidence | AI call |
| Patch Author | Produce the edit plan | AI call |
| Test Strategist | Select potentially relevant existing tests | Programmed logic |
| Patch Reviewer | Review the proposal’s supplied summary and context | AI call |

These are not six independent people or six parallel servers. The implementation runs specialist steps in sequence, and some are normal Python functions rather than AI calls.

Selecting test paths does not execute those tests. The model-based reviewer does not replace the programmed validator or the human review gate. Both modes use the same overall graph and review process.

**Follow this in code:** [specialist pipeline](../src/issue_to_patch/agents/pipeline.py) and [model interface](../src/issue_to_patch/llm/client.py).

## 11. How the code is organized

All application code lives under `src/issue_to_patch/`.

| Location | Simple responsibility |
| --- | --- |
| `api/` | Serve pages and receive browser/API requests |
| `auto_run.py` | Connect ingestion, indexing, and investigation |
| `ingestion/` | Fetch issue information and prepare repository snapshots |
| `processing/` | Turn source files into searchable chunks |
| `retrieval/` | Find and rank relevant chunks |
| `graph/` | Control investigation steps, state, retries, and pauses |
| `agents/` | Implement the optional specialist workflow |
| `llm/` | Communicate with the model or fake test provider |
| `patching/` | Apply edit plans, produce patches, validate, materialize, and push |
| `persistence/` | Save database information and audit records |
| `safety/` | Define permissions and other execution boundaries |
| `evaluation/` | Measure retrieval and investigation results |
| `config/` | Read environment settings |
| `cli.py` | Provide terminal commands |
| `pipeline.py` | Support the earlier deterministic edit-plan workflow |

FastAPI is the Python library used to expose HTTP operations. Pydantic checks the shapes of structured inputs and outputs. SQLAlchemy manages application database access. Git manages source versions and patches. LangGraph coordinates the investigation.

The UI is server-generated HTML with CSS and JavaScript, rather than a separate React application. The browser and CLI are different entry points into shared backend capabilities.

## 12. Running the project and understanding keys

From the project root, the standard local setup is:

```bash
make install
make migrate
make serve
```

`make install` uses `uv` to set up Python 3.12 and dependencies. `make migrate` updates the database schema. `make serve` starts the API development server.

Open `http://localhost:8000/ui/runs` for the interface. `/docs` is the interactive API reference.

Copy `.env.example` to `.env` if you have not already configured the project. Avoid overwriting an existing configuration. The settings use the `ITP_` prefix.

### The three keys that are easy to confuse

| Variable | Who uses it? | Purpose |
| --- | --- | --- |
| `ITP_API_KEY` | Your application server | Optional shared access credential for this app; it is not an AI-provider key |
| `ITP_LLM_API_KEY` | The configured model provider client | Authenticate requests to the real AI provider |
| `ITP_GITHUB_TOKEN` | The GitHub client | Authenticate GitHub access when needed |

The browser’s workspace API key field is for access to this app, including supported per-user keys. Do not put the model-provider key into that field.

For a real investigation, choose a real provider and a model supported by your account. For example, the setting names are:

```dotenv
ITP_LLM_PROVIDER=openai
ITP_LLM_API_KEY=YOUR_PROVIDER_KEY
ITP_LLM_MODEL=YOUR_SUPPORTED_MODEL_NAME
```

Those values are placeholders, not working credentials or model names. Provider/model configuration belongs on the server. Keep real keys out of committed files and screenshots.

`make demo` runs the deterministic calculator-style demonstration from `examples/try_sprint1.sh`. It illustrates applying a supplied edit plan rather than an AI independently finding a fix.

The optional Docker infrastructure is not required just to understand or run the default SQLite-based application. The existence of a service in Docker Compose does not mean the current request path uses it.

## 13. Tests, limitations, and common confusion

### How this project checks its own behavior

- **Unit tests** check small components, such as rendering a diff or choosing a workflow route.
- **Integration tests** check components working together, such as starting a run and loading its review page.
- **FakeLLM** provides controlled responses so tests do not need real model calls.
- **Retrieval evaluation** measures whether search finds relevant code on labeled examples.
- **Run evaluation** summarizes outputs and can use a model-based judge as an additional signal.

`make check` runs linting, type checking, and tests. These are tests of this application. They are different from running the tests of a downloaded repository that the application is trying to fix.

### Common questions

**Does the AI read the whole repository at once?**

No. The project indexes code and retrieves selected evidence for the model.

**Does a confidence of 0.9 prove the fix has a 90% chance of being correct?**

No. It is a model-provided confidence value used by the workflow, not a calibrated probability of correctness.

**Why does Start take time?**

Fetching a repository, indexing files, searching, and making model requests all take time. The current endpoint waits for that work rather than returning a background job immediately.

**Why is the result inconclusive?**

There may be too little evidence, a weak diagnosis, or a failure to produce a usable candidate. The project can stop without claiming success.

**Why was a patch rejected?**

Read the validation details and review decision. A patch may not apply, may change files outside scope, may contain blocked content, or may be rejected by a person.

**Why can I see only part of a file?**

The UI displays the patch’s changed sections and nearby context, not a complete file browser.

**Why do UI changes not appear immediately?**

Presentation assets are read by the Python module. Restart the server and refresh the page if the running process still has the older content; CSS/HTML changes may not trigger Python-only reload watching.

**Does approving update GitHub?**

No. Recording approval, creating a local branch, pushing, and opening a pull request are separate operations.

**What should I trust most?**

Inspect the actual changed code, evidence, and check results. Treat the AI explanation as a proposal to assess. Test the resulting change in the target project before treating it as a confirmed fix.

## 14. A small glossary and reading order

| Word | Meaning in this project |
| --- | --- |
| Backend | Python code that performs the work |
| Frontend | Browser interface you interact with |
| Endpoint | An API address for a particular operation |
| Snapshot | Repository copy tied to a specific version |
| Chunk | A smaller piece of source code prepared for search |
| Evidence | Retrieved information used during investigation |
| Citation | A reference back to a code location |
| Hypothesis | A possible explanation of the bug |
| Edit plan | Structured instructions for replacing or creating code |
| Patch/diff | Text describing added and removed lines |
| State | The workflow’s current collection of information |
| Checkpoint | Saved workflow state for resuming later |
| Artifact | A saved output file |
| Scope | File patterns defining allowed changes |
| Deterministic | Programmed behavior rather than an AI judgment |

To understand the code without reading everything at once:

1. Read [auto_run.py](../src/issue_to_patch/auto_run.py) to see the three main phases.
2. Read [graph/build.py](../src/issue_to_patch/graph/build.py) to see how the investigation steps connect.
3. Read [graph/state.py](../src/issue_to_patch/graph/state.py) to see what information moves between steps.
4. Read [graph/nodes.py](../src/issue_to_patch/graph/nodes.py) one function at a time for the actual work.
5. Read [graph/router.py](../src/issue_to_patch/graph/router.py) to understand retries and stopping rules.
6. Read [patching/validate.py](../src/issue_to_patch/patching/validate.py) to understand what “checked” means.
7. Read [tests/integration/test_ui.py](../tests/integration/test_ui.py) to see a small, controlled investigation exercised through the API and pages.

As you read each function, ask three questions: What information comes in? What work happens? What information comes out? That is enough to start following this project without already knowing every library it uses.
