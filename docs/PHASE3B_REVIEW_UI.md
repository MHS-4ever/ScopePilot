# Phase 3B: Lightweight Human Review UI

Phase 3B adds a buildless, same-origin browser interface for the Phase 3A review API. It uses plain HTML, CSS, and JavaScript served directly by FastAPI. There are no frontend dependencies, CDN resources, Node.js tools, extra containers, or external publishing actions.

## Open the UI

Start ScopePilot normally, then visit:

```text
http://localhost:8000/review
```

Enter the value configured as `SCOPEPILOT_REVIEW_KEY`. The browser sends it as `X-Review-Key` for review API requests. The key exists only in the page's JavaScript memory and is not written to local storage, session storage, cookies, URLs, HTML, or JavaScript source.

## Workflow

1. Connect with the reviewer key.
2. Select a completed analysis from the review queue.
3. Compare the original brief and scope with the read-only AI requirements and clarification questions.
4. Edit the independent human-review task copies.
5. Approve or reject every task.
6. For clarification-blocked tasks, record a real resolution and explanation before approval.
7. Confirm finalization. Finalized reviews remain read-only and are not published anywhere.

The queue explicitly labels drafts that contain zero proposed tasks. These can be opened and explicitly finalized as empty reviews.

The selected brief UUID is reflected in the local `?brief=` URL so a finalized review can be reopened read-only after a page reload and reviewer-key entry. The reviewer key is never placed in the URL.

## Revision conflicts

Every task update includes the current review and task revisions. The finalization request includes the current review revision. If another operator changes the review first, the backend returns `409`; the UI displays the error and offers **Refresh review** rather than overwriting newer work.

## Security and rendering

- Review credentials are entered at runtime and never embedded in assets.
- Client messages, requirements, notes, and task content are rendered with DOM `textContent`; HTML from client-provided strings is never interpreted.
- No external scripts, styles, fonts, images, or CDN resources are loaded.
- Backend validation remains authoritative. The UI's disabled approval button is only a usability safeguard.
- The UI never calls GitHub, Gemini, or n8n.

## Files

- `src/scopepilot/static/review/index.html`
- `src/scopepilot/static/review/styles.css`
- `src/scopepilot/static/review/app.js`
- `tests/test_review_ui.py`

## Verification

```powershell
.venv\Scripts\python -m pytest
docker compose --env-file .env.example config --quiet
```

