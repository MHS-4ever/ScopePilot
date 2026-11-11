# ScopePilot Phase 2 Manual Verification Guide

This guide describes how the human operator manually configures credentials, imports the analysis workflow into n8n, and runs the demonstration briefs through the Gemini-powered pipeline.

---

## 1. Prerequisites & Stack Status

Ensure the Docker stack is running:

```powershell
docker compose ps
```

FastAPI should be running at `http://localhost:8000` and n8n at `http://localhost:5678`.

---

## 2. n8n Credential Configuration

1. Open `http://localhost:5678` in your browser.
2. Navigate to **Credentials** -> **Add Credential**.
3. Create or verify the following three credentials:

### Credential 1: Webhook Header Auth
- **Type**: Header Auth
- **Name**: `ScopePilot Webhook Header Auth`
- **Header Name**: `X-Webhook-Key` (or your chosen header)
- **Header Value**: Choose a strong secret (e.g. `manual-test-webhook-secret-2026`)

### Credential 2: Backend API Key
- **Type**: Header Auth
- **Name**: `ScopePilot Backend API Key`
- **Header Name**: `X-API-Key`
- **Header Value**: The value of `SCOPEPILOT_API_KEY` from your local `.env` file

### Credential 3: Google Gemini API
- **Type**: Google Gemini Chat Model / Google PaLM API (`googlePalmApi`)
- **Name**: `ScopePilot Gemini API`
- **API Key**: Your Google AI Studio Gemini API key

---

## 3. Workflow Import & Activation

1. In n8n, go to **Workflows** -> **Import from File**.
2. Select `workflows/brief-analysis.json` from the project repository.
3. Verify that the nodes reference the credentials:
   - **Receive Analysis Request**: Uses `ScopePilot Webhook Header Auth`
   - **Claim Brief Analysis**: Uses `ScopePilot Backend API Key`
   - **ScopePilot Gemini Model**: Uses `ScopePilot Gemini API`
   - **Record Extraction Failure**: Uses `ScopePilot Backend API Key`
   - **Validate and Save Analysis**: Uses `ScopePilot Backend API Key`
   - **Record Validation Failure**: Uses `ScopePilot Backend API Key`
4. Set the workflow to **Active** (toggle in top right).

---

## 4. Running the Demonstration Briefs

Use Windows PowerShell to ingest a brief, trigger analysis, and read the draft.

### Helper: Set Environment Variables in PowerShell

```powershell
$apiKey = (Get-Content .env | Where-Object { $_ -like "SCOPEPILOT_API_KEY=*" } | ForEach-Object { $_.Split("=", 2)[1] })
$apiHeaders = @{ "X-API-Key" = $apiKey; "Content-Type" = "application/json" }
$webhookHeaders = @{ "X-Webhook-Key" = "manual-test-webhook-secret-2026"; "Content-Type" = "application/json" }
```

---

### Demonstration A: Clear Request (Reading Game)

1. Ingest brief:

```powershell
$briefA = @{
  source_id = "demo-a-001"
  client_id = "client-reading-game"
  project_name = "Reading Game"
  message = "Add a progress bar and fix mobile audio."
} | ConvertTo-Json

$respA = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/briefs" -Headers $apiHeaders -Body $briefA
$briefAId = $respA.id
Write-Host "Ingested Brief A ID: $briefAId"
```

2. Trigger Analysis via n8n Webhook:

```powershell
$analyzeBodyA = @{ brief_id = $briefAId } | ConvertTo-Json
$analysisA = Invoke-RestMethod -Method Post -Uri "http://localhost:5678/webhook/scopepilot/analyze" -Headers $webhookHeaders -Body $analyzeBodyA
$analysisA | ConvertTo-Json -Depth 6
```

3. Expected Result Check:
- `requirements`: Two items (`R1`: Progress bar, `R2`: Mobile audio bug)
- `scope_assessment.status`: `"unknown"` (since `original_scope` was omitted)
- `deadline_quote`: `null`

---

### Demonstration B: Vague Feedback (Learning Platform)

1. Ingest brief:

```powershell
$briefB = @{
  source_id = "demo-b-001"
  client_id = "client-learning-plat"
  project_name = "Learning Platform"
  message = "The interface doesn't feel right. Please make it better."
} | ConvertTo-Json

$respB = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/briefs" -Headers $apiHeaders -Body $briefB
$briefBId = $respB.id
Write-Host "Ingested Brief B ID: $briefBId"
```

2. Trigger Analysis via n8n Webhook:

```powershell
$analyzeBodyB = @{ brief_id = $briefBId } | ConvertTo-Json
$analysisB = Invoke-RestMethod -Method Post -Uri "http://localhost:5678/webhook/scopepilot/analyze" -Headers $webhookHeaders -Body $analyzeBodyB
$analysisB | ConvertTo-Json -Depth 6
```

3. Expected Result Check:
- `clarification_questions`: Contains questions probing what interface aspects need improvement.
- `proposed_tasks`: Empty or blocked by clarification.
- No fabricated UI design claims.

---

### Demonstration C: Scope Change (Quiz Application)

1. Ingest brief with baseline original scope:

```powershell
$briefC = @{
  source_id = "demo-c-001"
  client_id = "client-quiz-app"
  project_name = "Quiz Application"
  message = "The quiz works. Please add multiplayer support and real-time leaderboards."
  original_scope = "Build a single-player quiz application with local score tracking."
} | ConvertTo-Json

$respC = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/briefs" -Headers $apiHeaders -Body $briefC
$briefCId = $respC.id
Write-Host "Ingested Brief C ID: $briefCId"
```

2. Trigger Analysis via n8n Webhook:

```powershell
$analyzeBodyC = @{ brief_id = $briefCId } | ConvertTo-Json
$analysisC = Invoke-RestMethod -Method Post -Uri "http://localhost:5678/webhook/scopepilot/analyze" -Headers $webhookHeaders -Body $analyzeBodyC
$analysisC | ConvertTo-Json -Depth 6
```

3. Expected Result Check:
- `scope_assessment.status`: `"potential_change"`
- `scope_assessment.reason`: Mentions extending beyond single-player baseline.

---

## 5. Direct Read Verification

Verify that the draft analysis can be queried directly from FastAPI:

```powershell
Invoke-RestMethod -Method Get -Uri "http://localhost:8000/api/v1/briefs/$briefCId/analysis" -Headers $apiHeaders | ConvertTo-Json -Depth 6
```

Confirm that `claim_token` is not present in the output and `status` is `"draft"`.

---

## 6. Live Gemini Verification Findings & Regression Corrections

### Live Demonstration Outcomes:
1. **Reading Game (Clear Request)**:
   - 2 distinct requirements extracted (`R1`: progress bar, `R2`: mobile audio bug).
   - 2 clarification questions and 2 blocked tasks generated.
   - `scope_assessment.status`: `"unknown"` (as `original_scope` was `null`).
   - No deadline invented.

2. **Learning Platform (Vague Feedback)**:
   - Preserved subjective feedback uncertainty.
   - Generated clarifying questions on UI/UX preferences.
   - Avoided fabricating imaginary tasks or arbitrary design commitments.

3. **Quiz Application (Scope Change)**:
   - Identified multiplayer and real-time leaderboards.
   - Successfully flagged `scope_assessment.status`: `"potential_change"` against the single-player baseline scope.

### Manual Verification Defect & Fixes Applied:
- **Extractor Input Mapping**: Corrected `Extract Requirements` text expression to use:
  ```javascript
  {{ JSON.stringify({
    project_name: $('Claim Brief Analysis').first().json.body.brief.project_name,
    message: $('Claim Brief Analysis').first().json.body.brief.message,
    original_scope: $('Claim Brief Analysis').first().json.body.brief.original_scope
  }) }}
  ```
- **Model Metadata Alignment**: Synchronized `Validate and Save Analysis` to record `model_id: "gemini-3.5-flash-lite"`, exactly matching the configured `ScopePilot Gemini Model` node (`gemini-3.5-flash-lite`).
  > [!NOTE]
  > In n8n 2.47.3 UI, the model selector may show an "unsupported-value" badge for `gemini-3.5-flash-lite` because it is newer than the static dropdown list in the pinned image. It functions as expected at runtime against Google Gemini API.
- **Cached Draft Response**: Added `Get Existing Draft` (`GET /api/v1/briefs/{brief_id}/analysis`) on the `already_completed` branch so cached responses return the identical `AnalysisReadResponse` contract as new completions without invoking Gemini.
- **Clarification Questions Schema & Prompt Hardening (Library Application Bug)**:
  - In a test with the brief `"Please add a search box to the library page and fix the broken login button."`, Gemini initially produced nested objects for clarification questions: `[{"clarification_question": "..."}]` instead of plain strings `["..."]`. This caused n8n's parser to throw `OUTPUT_PARSING_FAILURE`.
  - When an ungrounded correction prompt was attempted without original message context, an empty extraction was generated falsely stating no brief was supplied.
  - **Resolution**:
    1. Hardened `prompts/brief-extraction-v1.md` and `schemas/analysis-output.schema.json` with strict rules and positive/negative few-shot examples explicitly forbidding nested objects in `clarification_questions`.
    2. Implemented an automated quality safeguard in FastAPI (`src/scopepilot/analysis_schemas.py`) that rejects empty requirements on actionable briefs with `422 Unprocessable Entity`, transitioning the brief to `failed` (`invalid_model_output`) rather than persisting corrupt drafts.
