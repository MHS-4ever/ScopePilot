# ScopePilot Phase 2 Architecture

## Overview

ScopePilot Phase 2 implements an AI-powered requirement extraction pipeline for freelance software-development client briefs previously ingested in Phase 1.

The pipeline is triggered via an authenticated webhook, claims the brief atomically in FastAPI, retrieves the uncorrupted brief content, invokes the Google Gemini chat model via n8n's native Information Extractor, validates the model output against strict Pydantic v2 schemas and source text evidence, and persists the validated analysis as a **DRAFT**.

```text
Existing Brief in SQLite
    |
    v
Authenticated Analysis Webhook (n8n: POST /webhook/scopepilot/analyze)
    |
    v
FastAPI Atomic Analysis Claim (POST /api/v1/briefs/{brief_id}/analysis/claim)
    |
    v
Retrieve Stored Brief Content (FastAPI -> n8n)
    |
    v
n8n Information Extractor (JSON Schema + System Prompt)
    |
    v
Google Gemini Chat Model (gemini-3.5-flash-lite, Temp: 0)
    |
    v
FastAPI Pydantic & Source-Evidence Validation (POST /api/v1/briefs/{brief_id}/analysis/complete)
    |
    v
Persist Validated Analysis in SQLite (status: draft)
    |
    v
Return Draft Analysis Result to Webhook Caller
```

---

## Boundaries & Non-Goals

Phase 2 deliberately restricts scope to requirement extraction and validation:
- **No Publication or Approval UI**: Completed analyses are strictly drafts. No task may be approved or published in Phase 2.
- **No GitHub Issues / Projects**: No external issue creation occurs.
- **No External Messaging Ingress**: Slack, Gmail, WhatsApp, and Upwork ingestion belong to future phases.
- **No Distributed Infrastructure**: No Redis, Celery, vector databases, or message queues. Concurrency is handled directly through SQLite conditional updates and transactions.

---

## Database Design

Phase 2 adds exactly one new table (`brief_analyses`) to the SQLite database without modifying the existing `briefs` table or records.

### `brief_analyses` Table Schema

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `VARCHAR(36)` | PRIMARY KEY | Unique analysis UUID |
| `brief_id` | `VARCHAR(36)` | NOT NULL, UNIQUE, FK -> `briefs.id` | Exactly one analysis per brief |
| `status` | `VARCHAR(20)` | NOT NULL | State: `processing`, `draft`, `failed` |
| `claim_token` | `VARCHAR(36)` | NOT NULL | Active claim token (UUID) |
| `attempt_count` | `INTEGER` | NOT NULL, DEFAULT 1 | Total number of processing attempts |
| `claimed_at` | `VARCHAR(32)` | NOT NULL | ISO 8601 UTC timestamp of active claim |
| `lease_expires_at` | `VARCHAR(32)` | NOT NULL | ISO 8601 UTC timestamp when claim expires (10 min lease) |
| `created_at` | `VARCHAR(32)` | NOT NULL | ISO 8601 UTC creation timestamp |
| `updated_at` | `VARCHAR(32)` | NOT NULL | ISO 8601 UTC last update timestamp |
| `completed_at` | `VARCHAR(32)` | NULLABLE | ISO 8601 UTC completion timestamp |
| `model_id` | `VARCHAR(100)` | NULLABLE | LLM model identifier (e.g., `gemini-3.5-flash-lite`) |
| `prompt_version` | `VARCHAR(50)` | NULLABLE | Instruction prompt version (`brief-extraction-v1`) |
| `schema_version` | `VARCHAR(20)` | NULLABLE | Schema version (`1`) |
| `result_json` | `TEXT` | NULLABLE | Canonical serialized JSON of validated `AnalysisResult` |
| `last_error_code` | `VARCHAR(50)` | NULLABLE | Safe failure code (`model_failed`, `invalid_model_output`, etc.) |

### SQLite Foreign Key & Concurrency Configuration
- `PRAGMA foreign_keys=ON` is configured on database connection alongside `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=30000`.
- Short-lived transactions ensure database locks are not held during external Gemini invocations.

---

## State Machine & Concurrency

```text
[No Analysis] 
     │
     │ claim (201)
     ▼
[processing] ───────── complete (200) ─────────► [draft] (Terminal in Phase 2)
     │
     │ fail (409/fail)
     ▼
  [failed]
     │
     │ reclaim (201)
     ▼
[processing] (new claim_token, attempt_count + 1)
```

### Transition Rules:
1. `NO ANALYSIS -> processing`: First claim initializes record with `attempt_count = 1` and 10-minute lease.
2. `processing -> draft`: Completed only when matching active `claim_token` is presented before lease expiry, and full Pydantic + source validation passes.
3. `processing -> failed`: Safe error code recorded only by active `claim_token`.
4. `failed -> processing`: Reclaim atomically generates a new `claim_token`, increments `attempt_count`, and resets lease.
5. `processing (expired lease) -> processing`: Expired lease is atomically reclaimed with rotated `claim_token`.
6. `draft -> *`: Terminal for Phase 2. Duplicate completions or re-claims are rejected or return the existing draft (`200 OK`, `outcome = "already_completed"`).

---

## Data Contract & Validation

The output structure strictly adheres to `schemas/analysis-output.schema.json` and `scopepilot.analysis_schemas.AnalysisResult`:

1. **`requirements`**: List of up to 20 requirements with sequential IDs (`R1`, `R2`, ...). Each `source_quote` must exist as an exact continuous substring in the client message.
2. **`clarification_questions`**: Array of up to 20 unique clarifying questions (max 300 characters each).
3. **`proposed_tasks`**: Up to 20 actionable tasks referencing existing requirement IDs. If `requirements` is empty, `proposed_tasks` must be empty.
4. **`scope_assessment`**: Compares requested work against `original_scope`. If `original_scope` is null/empty, status MUST be `unknown`.
5. **`deadline_quote`**: Either `null` or an exact continuous substring of the client message. No calendar dates are invented.

---

## Security Model

1. **Client Content Isolation**: Client messages and scope are treated as untrusted data inputs. Instructions in client text are never executed.
2. **Zero Credential Exposure**:
   - `claim_token` is never exposed in public webhook responses or `GET` read endpoints.
   - No API keys are embedded in exported workflow JSON.
   - Gemini API keys are configured strictly within n8n's credential vault.
   - FastAPI never calls Gemini directly.
3. **Draft Immutability**: A draft cannot be overwritten, modified, or failed once validated.
