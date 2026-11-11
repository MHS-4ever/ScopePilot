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

