# Phase 3A: Human Review Backend

Phase 3A adds a local operator-only review API for Phase 2 analysis drafts. It does not add a browser UI, GitHub publishing, new n8n workflows, or external network calls.

## Authentication

Every review endpoint requires a separate header:

```text
X-Review-Key: <SCOPEPILOT_REVIEW_KEY>
```

`X-API-Key` does not authorize review operations. If `SCOPEPILOT_REVIEW_KEY` is unset or empty, review endpoints fail closed with `401` while health, ingestion, and analysis endpoints continue to work.

Generate and configure an independent value in `.env`:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Persistence

`Base.metadata.create_all()` safely adds two tables to the existing SQLite database without changing or deleting existing tables:

### `brief_reviews`

- UUID primary key
- Unique foreign key to `briefs.id`
- Unique foreign key to `brief_analyses.id`
- Status: `in_review` or `finalized`
- Global optimistic `revision`
- UTC creation, update, and optional finalization timestamps

### `review_tasks`

- Stable UUID primary key
- Foreign key to `brief_reviews.id`
- Unique `(review_id, position)` ordering key
- Copied task type, title, description, requirement IDs, and acceptance criteria
- Immutable `original_blocked_by_clarification` audit flag
- Decision: `pending`, `approved`, or `rejected`
- Optional review note
- Clarification resolution state and explanation
- Per-task optimistic `revision`
- UTC creation and update timestamps

The original `brief_analyses.result_json` is never updated by review operations.

## API contracts

All models reject unknown fields.

### Review queue

```http
GET /api/v1/review-queue
X-Review-Key: ...
```

Returns valid Phase 2 drafts that are ready for review or currently in review. Finalized reviews are excluded.

### Initialize review

```http
POST /api/v1/briefs/{brief_id}/review
X-Review-Key: ...
```

The first request returns `201`; repeated or concurrent initialization returns the existing review with `200`. Only a valid analysis with `status = draft` is eligible. Task UUIDs remain stable.

### Read review

```http
GET /api/v1/briefs/{brief_id}/review
X-Review-Key: ...
```

Returns brief context, source requirements and clarification questions, analysis metadata, review state, and editable task copies. It never returns an analysis claim token or any credential.

### Edit or decide a task

```http
PATCH /api/v1/briefs/{brief_id}/review/tasks/{task_id}
X-Review-Key: ...
Content-Type: application/json

{
  "review_revision": 1,
  "task_revision": 1,
  "title": "Implement title and author search",
  "requirement_ids": ["R1"],
  "acceptance_criteria": [
    "Users can search by title",
    "Users can search by author"
  ],
  "decision": "approved",
  "review_note": "Confirmed with the client",
  "clarification_resolved": true,
  "clarification_resolution": "Client confirmed title and author fields."
}
```

Both revision values are required. A stale task or review revision returns `409`, preventing lost updates and races with finalization. Requirement IDs must exist in the immutable Phase 2 result or the API returns `422`.

A task originally marked `blocked_by_clarification = true` cannot be approved unless `clarification_resolved` is true and a nonempty `clarification_resolution` is stored. Rejection does not require resolution.

### Finalize review

```http
POST /api/v1/briefs/{brief_id}/review/finalize
X-Review-Key: ...
Content-Type: application/json

{
  "review_revision": 3
}
```

Every task must be explicitly approved or rejected. All-rejected reviews are valid. Finalization increments the revision and makes the review and tasks immutable. Repeated finalization is idempotent and returns the existing finalized review. It never publishes externally.

## Error behavior

- `401`: missing, invalid, or unconfigured review key
- `404`: brief, review, or review task does not exist
- `409`: ineligible analysis state, stale revision, incomplete finalization, or finalized immutability
- `422`: malformed request, invalid requirement reference, or clarification-gate violation

## Local verification

```powershell
.venv\Scripts\python -m pytest
docker compose config
```

