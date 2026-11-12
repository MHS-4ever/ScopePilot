# Phase 4: GitHub Issues Publishing

## Configuration

Create a fine-grained personal access token in GitHub under **Settings → Developer settings → Personal access tokens → Fine-grained tokens**. Restrict repository access to the selected repository and grant **Issues: Read and write**. Do not grant unrelated permissions.

Set these values in the local `.env` file:

```text
SCOPEPILOT_GITHUB_TOKEN=<fine-grained-token>
SCOPEPILOT_GITHUB_REPO=owner/repository
```

The token is read only by FastAPI. It is not sent to the browser, stored in SQLite, included in API responses, or logged. If either setting is absent or the repository format is invalid, the rest of ScopePilot continues to work and the publish endpoint returns `503`.

## Publishing workflow

1. Complete the normal human review and finalize it.
2. Reopen the finalized review through its `?brief=<UUID>` link if necessary.
3. Confirm the approved-task count and select **Publish approved tasks**.
4. Confirm the prompt, then review the per-task result and GitHub issue links.

The API endpoint is:

```text
POST /api/v1/briefs/{brief_id}/review/publish
X-Review-Key: <review-key>
```

Only approved tasks from a finalized review are eligible. Issue bodies contain the reviewed description, linked requirement IDs, acceptance criteria, ScopePilot review/task references, and a stable task marker. They do not contain the original client brief, reviewer key, token, or analysis claim token.

## Duplicate protection and reconciliation

`task_publications` has a database uniqueness constraint on the review task and target repository. The row is committed in `publishing` state before the GitHub request. Successful creation records the issue number and canonical URL.

Timeouts, transport failures, rate limits, and server errors are marked `requires_reconciliation`. Repeating the publish action searches GitHub for the stable task marker. It records a matching issue as published, but it does not create a new issue when the prior result is uncertain.

## Known limitations

- GitHub Search can take time to index a newly created issue; reconciliation may need to be repeated later.
- A definitive failed request can be retried by repeating publication after fixing the configuration or permission problem.
- Publication is synchronous and intended for a small local review queue.
- Phase 4 does not publish labels, milestones, assignees, comments, or GitHub Projects entries.
