# ScopePilot

ScopePilot Phase 1 is a local, authenticated brief-ingestion foundation for a future freelance workflow automation system. It accepts validated client briefs, stores them in SQLite, and safely deduplicates retries from n8n. This phase intentionally contains no frontend, LLM, GitHub Issues, cloud services, PostgreSQL, or Redis.

## Architecture

```text
caller
  | POST + webhook header credential
  v
n8n :5678  Webhook -> HTTP Request -> Respond to Webhook
                         |
                         | POST + backend header credential
                         v
                    FastAPI :8000
                         |
                         v
                  SQLite volume (UTC timestamps)
```

Docker Compose runs exactly two services. Host ports bind to `127.0.0.1`; n8n reaches FastAPI through the Docker service name `http://api:8000`. Named volumes retain n8n state and `/app/data/scopepilot.db`. n8n scheduling uses `Asia/Karachi`; API timestamps are generated and stored as UTC ISO 8601 values.

The API uses `(client_id, source_id)` as a database-level unique key. A SHA-256 fingerprint is calculated from canonical JSON containing all validated input fields. An identical retry returns the original brief. Reusing the key with changed content returns `409 Conflict`. If two requests race, the unique constraint selects the winner and the losing transaction safely reads and returns that row.

The schema is initialized reproducibly with SQLAlchemy metadata during API startup. That is sufficient for the single Phase 1 schema; introduce a migration tool before making an incompatible production schema change.

## Requirements

- Windows 10/11 with Docker Desktop using Linux containers
- PowerShell
- Optional local testing: Python 3.12

The n8n image is pinned to `docker.n8n.io/n8nio/n8n:2.47.3`. It was the upstream `stable` release on 2026-11-09; see the [official n8n releases](https://github.com/n8n-io/n8n/releases). Pinning prevents an unreviewed automatic upgrade.

## Docker Desktop setup

From the repository directory:

```powershell
Copy-Item .env.example .env
```

Replace both placeholder values in `.env`. Generate independent secrets, for example:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set the first result as `SCOPEPILOT_API_KEY` and the second as `N8N_ENCRYPTION_KEY`, then start the stack:

```powershell
docker compose config
docker compose up --build -d
docker compose ps
docker compose logs api n8n
```

Open `http://localhost:5678` for n8n. FastAPI documentation is available locally at `http://localhost:8000/docs`; `GET http://localhost:8000/health` is public.

Keep `N8N_ENCRYPTION_KEY` stable after credentials are saved. Losing or changing it can make stored n8n credentials unreadable. `.env`, SQLite data, and Docker volume contents are ignored by Git.

## API

Authenticated endpoints require `X-API-Key: <SCOPEPILOT_API_KEY>`:

- `POST /api/v1/briefs`
- `GET /api/v1/briefs/{brief_id}`

`GET /health` does not require authentication. Input limits are 200 characters for each identifier, 300 for `project_name`, and 20,000 each for `message` and optional `original_scope`. Unknown fields and whitespace-only values are rejected.

Example direct request:

```powershell
$headers = @{ "X-API-Key" = (Get-Content .env | Where-Object { $_ -like "SCOPEPILOT_API_KEY=*" } | ForEach-Object { $_.Split("=", 2)[1] }) }
$body = @{
  source_id = "email-2026-001"
  client_id = "client-42"
  project_name = "Customer portal refresh"
  message = "Please rebuild the customer portal and preserve SSO."
  original_scope = "Discovery and implementation"
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/briefs -Headers $headers -ContentType "application/json" -Body $body
```

A successful response is `201 Created` (including an identical retry) and resembles:

```json
{
  "source_id": "email-2026-001",
  "client_id": "client-42",
  "project_name": "Customer portal refresh",
  "message": "Please rebuild the customer portal and preserve SSO.",
  "original_scope": "Discovery and implementation",
  "id": "35bba281-aea3-4c57-b986-2d951e28af1c",
  "received_at": "2026-11-11T09:30:00Z",
  "status": "received"
}
```

Expected errors are `401` for a missing/invalid key, `404` for an unknown brief ID, `409` when an existing `(client_id, source_id)` has different content, and `422` for invalid input.

## n8n first login, import, and credentials

1. Open `http://localhost:5678` and create the local owner account on first login. Do not use real client data for setup.
2. Open **Credentials**, create a **Header Auth** credential named `ScopePilot Webhook Header Auth`, choose a private caller-facing header such as `X-Webhook-Key`, and enter a strong independent secret.
3. Create another **Header Auth** credential named `ScopePilot Backend API Key`. Set the header name to `X-API-Key` and its value to exactly `SCOPEPILOT_API_KEY` from `.env`.
4. Open **Workflows**, choose **Import from File**, and select `workflows/brief-ingestion.json`.
5. Open the **Receive Brief** node and select the webhook credential. Open **Send to ScopePilot API** and select the backend credential. Credential names are included in the export as hints, but secret values and credential IDs are deliberately absent.
6. Save and activate the workflow. Copy its production webhook URL. It should be `http://localhost:5678/webhook/scopepilot/briefs` with the supplied Compose configuration.

The workflow forwards the webhook JSON body to `http://api:8000/api/v1/briefs`, preserves the backend response body and status code, and returns them to the caller.

Manual production-webhook test (replace the header name and secret if you chose different values):

```powershell
$webhookHeaders = @{ "X-Webhook-Key" = "replace-with-your-webhook-secret" }
$body = @{
  source_id = "manual-001"
  client_id = "demo-client"
  project_name = "Demo project"
  message = "A test brief sent through n8n."
} | ConvertTo-Json
Invoke-WebRequest -Method Post -Uri http://localhost:5678/webhook/scopepilot/briefs -Headers $webhookHeaders -ContentType "application/json" -Body $body
```

For n8n's editor test mode, click **Listen for test event** and use the displayed `/webhook-test/` URL instead. Webhook Header Auth controls caller access; the backend credential is separate so the API key is never embedded in workflow JSON.

## Local tests

Use Python 3.12 and a virtual environment:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"
.venv\Scripts\python -m pytest
```

Run the API outside Docker only after setting local environment variables:

```powershell
$env:SCOPEPILOT_API_KEY = "local-development-key"
$env:SCOPEPILOT_DATABASE_URL = "sqlite:///./data/scopepilot.db"
.venv\Scripts\python -m uvicorn scopepilot.main:app --reload
```

## Operations and limitations

- Phase 1 has no approval flow, LLM extraction, GitHub publishing, frontend, users/roles, rate limiting, or external ingress/TLS.
- SQLite is appropriate for this local single-API deployment, not a horizontally scaled API cluster.
- Schema initialization creates missing tables but does not migrate incompatible existing tables.
- The application does not log request bodies or full client messages. Uvicorn still emits normal access metadata.
- n8n 2.47.3's CLI successfully imports the workflow JSON. A full credential-backed webhook execution still requires the first-login and credential setup above; that UI-dependent path is not claimed as tested.
- Back up both Docker volumes before upgrades. Stop the stack with `docker compose down`; adding `-v` deletes persistent application and n8n data and should only be used intentionally.

---

# ScopePilot Phase 2: AI-Powered Requirement Extraction

Phase 2 introduces an AI analysis pipeline for previously ingested briefs. It uses Google Gemini via an authenticated n8n workflow to extract structured requirements, proposed development tasks, clarification questions, and scope change assessments. The output is strictly validated and persisted as a **DRAFT**.

> [!IMPORTANT]
> **Distinction of Concepts:**
> - **AI-Generated Proposals**: Unvalidated, raw LLM suggestions returned by Gemini.
> - **Validated Analysis**: Strict, schema-compliant, source-grounded draft persisted in the database after backend Pydantic validation.
> - **Human Approval**: Interactive human review (belongs to Phase 3; does not exist in Phase 2).
> - **Published GitHub Issues**: External tracking records (belongs to Phase 4; does not exist in Phase 2).
> 
> In Phase 2, **only the first two exist**. No tasks are published automatically.

## Phase 2 Architecture

```text
Existing Brief in SQLite
    │
    ▼
Authenticated Webhook (n8n: POST /webhook/scopepilot/analyze)
    │
    ▼
FastAPI Atomic Analysis Claim (POST /api/v1/briefs/{brief_id}/analysis/claim)
    │
    ▼
Claimed Brief Returned to n8n
    │
    ▼
n8n Information Extractor (JSON Schema + System Prompt)
    │
    ▼
Google Gemini Chat Model (gemini-3.5-flash-lite, Temp: 0)
    │
    ▼
FastAPI Validation & Persistence (POST /api/v1/briefs/{brief_id}/analysis/complete)
    │
    ▼
Persist Validated Analysis in SQLite (status = draft)
    │
    ▼
Return Validated Result to Caller
```

## Database Design: `brief_analyses` Table

Phase 2 adds exactly one new table (`brief_analyses`) without altering the existing `briefs` table:

- `id`: String(36) UUID primary key
- `brief_id`: String(36) Foreign key to `briefs.id`, UNIQUE, NOT NULL
- `status`: String(20) (`processing`, `draft`, `failed`)
- `claim_token`: String(36) UUID identifying the active claim attempt
- `attempt_count`: Integer starting at 1
- `claimed_at`: String(32) UTC ISO 8601 timestamp
- `lease_expires_at`: String(32) UTC ISO 8601 timestamp (10-minute lease)
- `created_at`: String(32) UTC ISO 8601 timestamp
- `updated_at`: String(32) UTC ISO 8601 timestamp
- `completed_at`: Nullable String(32) UTC ISO 8601 timestamp
- `model_id`: Nullable String(100) (e.g., `gemini-3.5-flash-lite`)
- `prompt_version`: Nullable String(50) (`brief-extraction-v1`)
- `schema_version`: Nullable String(20) (`1`)
- `result_json`: Nullable Text serialized JSON of validated analysis
- `last_error_code`: Nullable String(50) safe failure code

SQLite foreign keys are enforced via `PRAGMA foreign_keys=ON` on connection.

## Analysis State Machine & Lease Recovery

Transitions:
- `NO ANALYSIS -> processing`: First claim succeeds (`201 Created`).
- `processing -> draft`: Successful completion with active token (`200 OK`). Terminal state for Phase 2.
- `processing -> failed`: Active claim records failure (`200 OK`).
- `failed -> processing`: Reclaim atomically assigns a new `claim_token` and increments `attempt_count` (`201 Created`).
- `processing (lease expired) -> processing`: Claims older than 10 minutes can be reclaimed with a new `claim_token` and incremented `attempt_count`.
- `draft -> *`: Cannot be re-claimed or failed. Calling `/claim` on an existing draft returns `200 OK` with `outcome = "already_completed"` and the saved result (no `claim_token`).

## New API Endpoints

All Phase 2 endpoints require `X-API-Key: <SCOPEPILOT_API_KEY>`:

1. `POST /api/v1/briefs/{brief_id}/analysis/claim`
   - Request: `{}`
   - Response `201`: `{"outcome": "claimed", "analysis_id": "<uuid>", "claim_token": "<uuid>", "brief": { ... }, "prompt_version": "brief-extraction-v1", "schema_version": "1"}`
   - Response `200`: `{"outcome": "already_completed", "analysis_id": "<uuid>", "brief": { ... }, "result": { ... }}`
   - Response `409`: Analysis in progress (`{"detail": "analysis_in_progress"}`)
2. `POST /api/v1/briefs/{brief_id}/analysis/complete`
   - Request: `{"claim_token": "<uuid>", "model_id": "...", "prompt_version": "...", "schema_version": "...", "result": { ... }}`
   - Response `200`: Validated draft analysis
   - Response `422`: Schema or source-evidence validation failure
   - Response `409`: Stale claim token or lease expired
3. `GET /api/v1/briefs/{brief_id}/analysis`
   - Response `200`: Current analysis state, attempt count, metadata, validated draft (when `draft`), safe error code (when `failed`). Does **not** include `claim_token`.
   - Response `404`: Analysis or brief not found
4. `POST /api/v1/briefs/{brief_id}/analysis/fail`
   - Request: `{"claim_token": "<uuid>", "error_code": "model_failed" | "invalid_model_output" | "transport_failure" | "internal_error"}`
   - Response `200`: Safe failure record

## Structured Output Schema & Prompt

- **Canonical JSON Schema**: `schemas/analysis-output.schema.json`
- **Versioned Extraction Prompt**: `prompts/brief-extraction-v1.md`
<<<<<<< HEAD
- **Model Selection & Synchronization**: `gemini-3.5-flash-lite` (configured with temperature 0, no external tools, no search grounding). The `model_id` passed in `Validate and Save Analysis` matches this exact string.
  > [!NOTE]
  > In n8n 2.47.3, the model dropdown may display an "unsupported-value" badge for `gemini-3.5-flash-lite` because the model identifier was introduced upstream after n8n 2.47.3 was tagged. It is fully supported and operational at runtime via Google Gemini API.
- **Extraction Reliability & Quality Safeguard**:
  - The extraction prompt and canonical schema explicitly enforce `clarification_questions` as a flat array of plain strings (strictly forbidding nested objects like `[{"clarification_question": "..."}]`).
  - The FastAPI completion endpoint enforces an automated safeguard against hallucinated empty drafts: if an extraction returns zero requirements on a client message containing clear actionable request indicators (or falsely claims no brief was supplied), the request is rejected with `422 Unprocessable Entity`, allowing n8n to safely record an `invalid_model_output` failure rather than saving a corrupt draft.
=======
- **Model Selection**: `gemini-3.5-flash-lite` (or available Gemini model in user's free tier, configured with temperature 0, no external tools, no search grounding).
>>>>>>> 7c20d0b3ab1986a53e8c97b03f47c489bf0a7d69

## n8n Analysis Workflow

- Workflow file: `workflows/brief-analysis.json`
- Webhook path: `scopepilot/analyze`
- Webhook Header Auth Credential: `ScopePilot Webhook Header Auth`
- Backend API Key Credential: `ScopePilot Backend API Key`
- Gemini API Credential: `ScopePilot Gemini API` (`googlePalmApi` type)

### Import & Setup in n8n:
1. Open `http://localhost:5678` -> **Workflows** -> **Import from File** -> select `workflows/brief-analysis.json`.
2. Configure credentials in n8n:
   - Header Auth: `ScopePilot Webhook Header Auth`
   - Header Auth: `ScopePilot Backend API Key`
   - Google PaLM API / Gemini: `ScopePilot Gemini API`
3. Activate the workflow.

<<<<<<< HEAD
### Live Gemini Verification Findings & Workflow Refinements:
- **Input Mapping**: Claim response is a full HTTP object. The `Extract Requirements` node accesses brief fields via:
  `{{ JSON.stringify({ project_name: $('Claim Brief Analysis').first().json.body.brief.project_name, message: $('Claim Brief Analysis').first().json.body.brief.message, original_scope: $('Claim Brief Analysis').first().json.body.brief.original_scope }) }}`.
- **Cached Draft Response Consistency**: When a brief is already completed (`200 OK` on claim), the workflow routes through `Get Existing Draft` (`GET /api/v1/briefs/{brief_id}/analysis`), returning the uniform `AnalysisReadResponse` structure without re-invoking Gemini.
- **Demonstration Results**:
  1. *Reading Game*: Extracted 2 distinct requirements, 2 clarification questions, and marked scope as `unknown` with no invented deadline.
  2. *Learning Platform*: Captured subjective feedback as unclear, asked clarifying questions, and avoided fabricating imaginary features.
  3. *Quiz Application*: Identified multiplayer and leaderboards as `potential_change` against the single-player baseline scope.

=======
>>>>>>> 7c20d0b3ab1986a53e8c97b03f47c489bf0a7d69
## Automated Tests

Run all tests via pytest in PowerShell:

```powershell
.venv\Scripts\python -m pytest
```

Test counts:
<<<<<<< HEAD
- **67 passed, 0 failed** (12 Phase 1 tests + 15 Data Contract & Safeguard tests + 27 State Management & Security tests + 13 Workflow Contract & Regression tests).
=======
- **56 passed, 0 failed** (12 Phase 1 tests + 11 Data Contract tests + 27 State Management & Security tests + 6 Workflow tests).
>>>>>>> 7c20d0b3ab1986a53e8c97b03f47c489bf0a7d69

## Security Considerations

- **Untrusted Input**: Client messages are strictly treated as untrusted data. The prompt explicitly instructs the LLM not to execute embedded commands.
- **Source Verification**: All `source_quote` strings must exist verbatim in the original client message; fabricated quotes are rejected with `422 Unprocessable Entity`.
- **Secret Redaction**: API keys and claim tokens are never returned in public responses or logged.


