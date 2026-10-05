# Issue-to-Patch UI redesign

The interface should make three actions obvious: start an investigation, find a run, and review a proposed patch.

| Before | New experience |
| --- | --- |
| Unstructured form on a blank page | Persistent workspace navigation and a focused content area |
| Access key is the first field | GitHub issue URL is primary; scope and access key are in advanced settings |
| Empty table | Helpful first-run state with a link to the issue input |
| Raw status strings and identifiers | Readable status badges, issue-first rows, and secondary run IDs |
| No overview | Actual investigation, review, validation, and cost totals |
| Hard-to-scan history | Search by issue or run ID and filter by status |
| Plain investigation message | Accessible pending feedback, disabled submit, and recoverable errors |
| Unstyled review | Shared workspace shell, readable evidence and diff, distinct review actions |

## Visual direction

A restrained engineering workspace: charcoal-green navigation, a soft gray canvas, white panels, teal actions, amber review states, and green validation states. System fonts avoid external dependencies. Generous spacing and clear labels establish hierarchy. Tables scroll on small screens while forms and summary cards reflow.

## Implementation plan

1. Introduce a shared responsive shell and visual styles for both routes.
2. Reorganize the start form; preserve the existing API request and optional authentication.
3. Add real metrics, searchable history, status filters, and useful empty states.
4. Restyle investigation details, diagnosis, validation, patch diff, and human review.
5. Verify existing API/UI integration tests and lint; inspect browser rendering when browser tooling is available.

## Interaction principles

Do not invent live pipeline progress: the current endpoint waits for the full result. Show an indeterminate pending state and explain the expected wait. Preserve server-side HTML escaping. Use explicit labels, visible keyboard focus, native URL validation, live status announcements, and reduced-motion support. A review submission must prevent duplicate clicks and recover from network errors.
