# Resolution plans before an investigation

On **Browse issues from a repository**, each open issue has **Show resolution plan** next to its run action. Plans are generated only when clicked. Previous/Next lets you continue beyond the first 30 issues; pull requests are excluded. Counts describe the current page, not the repository total. GitHub's updated-order pagination can shift when issues change while you browse.

The preview fetches the issue and recent discussion, prepares and indexes a repository snapshot, retrieves code, and asks the configured LLM for a short brainstorming plan. It shows the problem, 3–5 suggested steps, code references, suggested checks, and open questions. Click a reference to inspect the exact saved excerpt. Nothing in this operation drafts a patch or changes GitHub.

**Regenerate** uses the same saved commit and keeps the previous successful preview if regeneration fails. **Start investigation** reuses that snapshot/index and passes the preview as provisional guidance. The investigation must still gather evidence, create and validate a patch, and reach human review. The preview does not claim a fix is correct or that tests passed. Investigations sharing a plan use separate build directories for their branches.

## Setup and limits

Run `make migrate` and restart the API to install the `issue_plans` table. The app uses your existing model/provider, GitHub, authentication, and retrieval configuration. Private repository cloning also requires working Git credentials on the server. Plan endpoints use the workspace access key saved by the UI; real per-user accounts can access only their own plans. Local/shared-key users retain their existing shared identity semantics.

Inputs are bounded to 12,000 issue characters, 20 recent comments within 12,000 characters, and at most 12 source excerpts totaling 32,000 characters. The preview identifies shortened discussion context. Output targets 150–250 words; the model may vary within the structured field limits. Suggested references are accepted only when they identify a supplied source excerpt.

Plans and model costs are saved separately from investigation run history. The model cost recorded is the configured LLM client's accounting, not an inclusive billing statement for embedding or GitHub services. Snapshots and excerpts are retained for later inspection. Deleting their files makes plan handoff unavailable; it will not silently download a different commit.

Background work uses FastAPI's in-process tasks, matching the current automatic-run architecture. **Run one API worker**: startup marks unfinished plan jobs interrupted so they can be retried. This is not a distributed task queue and does not automatically resume work across restarts. Completed previews survive restart in the database. The issue browser looks up saved plans for the current user when it loads, so reopening a preview does not generate a new plan. There is no separate plan-history screen.

## API

All plan endpoints use the application's authentication and rate limiting:

- `GET /issue-plans?issue_url=owner/repo%231`: saved-plan summaries for the current user; repeat `issue_url` for up to 30 issues.
- `POST /issue-plans`: `{ "issue_url": "https://github.com/owner/repo/issues/123", "request_id": "unique-client-request-id" }` → `202 { "plan_id": "…" }`. The request ID is optional. Existing plans for the same user and issue are reused even when the request ID changes. Use the regenerate endpoint to request a new answer.
- `GET /issue-plans/{plan_id}`: status, saved result, source excerpts, pinned commit, truncation flag, recorded model cost, and error.
- `POST /issue-plans/{plan_id}/regenerate`: restart a finished/failed job against its saved snapshot. An already-running job is reused.
- `GET /issue-plans/{plan_id}/source?chunk_id=…`: an excerpt from that plan's evidence, never a filesystem path supplied by the caller.

`POST /runs/auto` additionally accepts `plan_id`. The issue must match a ready plan. Snapshot/ref overrides are rejected when reusing a plan. Existing requests without `plan_id` retain their normal behavior.

Stages: `fetching`, `preparing`, `retrieving`, `writing`, then `ready` or `failed`. Restart recovery uses `interrupted`. The browser polls every two seconds while a job runs and cleans up on navigation. Multiple previews can be expanded independently.

Plan previews clone only the latest commit of the default branch, avoiding full repository history downloads. Repository downloads time out after two minutes and show a retryable error. Restart the server to apply this behavior to new requests; retry previews interrupted by the restart.

Repository indexing bounds embedding inputs by tokens: oversized chunks are split into segments and their vectors are pooled, while the full source excerpt stays in storage. Requests obey both per-input and batch token budgets. Planning failures report the stage and sanitized provider HTTP status, and server logs include the plan ID and exception type.

Set `ITP_EMBEDDING_MODEL=text-embedding-3-large` to use the larger embedding model independently of the planning LLM. Its vectors have 3,072 dimensions; existing small-model vectors must be rebuilt before using the new model. Both models still require token-bounded inputs. See the [OpenAI embeddings guide](https://developers.openai.com/api/docs/guides/embeddings).

Switching between the default small and large vector dimensions triggers a full embedding refresh when a saved index is searched. The old index is retained until all replacement vectors succeed; failed refreshes stop retrieval. If the provider reports exhausted credits or quota, add API credits or resolve the account quota before retrying.


Saved previews are restored when the issue list loads. Each issue shows No plan yet,
Plan in progress, Plan ready, or a failure/interruption status. View plan opens the
saved result without an LLM call; View progress resumes polling an active job.
Only Regenerate (or Retry on a failed job) requests new work.

`GET /issue-plans?issue_url=owner/repo%231` returns summaries of the current user's
latest plans for up to 30 issue URLs (repeat the query parameter). `POST /issue-plans`
returns the latest existing plan for that user and issue, even with a new request
ID; it never implicitly retries or regenerates an existing plan. Initial creation
is serialized in the supported single-worker deployment to avoid duplicate jobs.
