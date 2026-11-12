"use strict";

const state = {
  key: "",
  queue: [],
  current: null,
  currentBriefId: null,
};

const elements = {
  authPanel: document.querySelector("#auth-panel"),
  authForm: document.querySelector("#auth-form"),
  reviewKey: document.querySelector("#review-key"),
  workspace: document.querySelector("#workspace"),
  connection: document.querySelector("#connection-state"),
  status: document.querySelector("#app-status"),
  statusText: document.querySelector("#app-status-text"),
  refreshReview: document.querySelector("#refresh-review"),
  refreshQueue: document.querySelector("#refresh-queue"),
  queueSummary: document.querySelector("#queue-summary"),
  queueList: document.querySelector("#queue-list"),
  emptyDetail: document.querySelector("#empty-detail"),
  reviewDetail: document.querySelector("#review-detail"),
};

class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
  }
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function badge(text, tone = "") {
  return node("span", `badge ${tone}`.trim(), text);
}

function humanize(value) {
  return String(value).replaceAll("_", " ");
}

function formatDetail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg || "Invalid request").join("; ");
  }
  return "The request could not be completed.";
}

async function apiRequest(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-Review-Key", state.key);
  if (options.body) headers.set("Content-Type", "application/json");
  let response;
  try {
    response = await fetch(path, { ...options, headers });
  } catch (_error) {
    throw new ApiError(0, "Cannot reach ScopePilot. Check that the API is running.");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(response.status, formatDetail(body.detail));
  }
  return body;
}

function showStatus(message, kind = "info", refresh = false) {
  elements.status.hidden = false;
  elements.status.className = `app-status ${kind}`.trim();
  elements.statusText.textContent = message;
  elements.refreshReview.hidden = !refresh || !state.currentBriefId;
}

function clearStatus() {
  elements.status.hidden = true;
  elements.status.className = "app-status";
  elements.statusText.textContent = "";
  elements.refreshReview.hidden = true;
}

function handleError(error, fallback) {
  if (error instanceof ApiError && error.status === 401) {
    state.key = "";
    state.queue = [];
    state.current = null;
    state.currentBriefId = null;
    elements.authPanel.hidden = false;
    elements.workspace.hidden = true;
    elements.connection.classList.remove("connected");
    elements.connection.lastChild.textContent = " Not connected";
    showStatus("Reviewer authentication failed. Check the key and try again.", "error");
    elements.reviewKey.focus();
    return;
  }
  const message = error instanceof Error ? error.message : fallback;
  const stale = error instanceof ApiError && error.status === 409;
  showStatus(message || fallback, "error", stale);
}

async function loadQueue() {
  showStatus("Loading review queue…");
  try {
    const response = await apiRequest("/api/v1/review-queue");
    state.queue = response.items;
    renderQueue();
    elements.workspace.hidden = false;
    elements.authPanel.hidden = true;
    elements.connection.classList.add("connected");
    elements.connection.lastChild.textContent = " Review API connected";
    clearStatus();
    const requestedBrief = new URLSearchParams(window.location.search).get("brief");
    if (requestedBrief && !state.current) await loadReviewById(requestedBrief);
  } catch (error) {
    handleError(error, "Unable to load the review queue.");
  }
}

function setBriefUrl(briefId) {
  const url = new URL(window.location.href);
  url.searchParams.set("brief", briefId);
  window.history.replaceState({}, "", url);
}

async function loadReviewById(briefId) {
  state.currentBriefId = briefId;
  renderQueue();
  showStatus("Loading linked review…");
  try {
    state.current = await apiRequest(`/api/v1/briefs/${briefId}/review`);
    renderReview();
    clearStatus();
  } catch (error) {
    handleError(error, "Unable to open the linked review.");
  }
}

function renderQueue() {
  elements.queueList.replaceChildren();
  const count = state.queue.length;
  elements.queueSummary.textContent = count === 1 ? "1 draft needs attention" : `${count} drafts need attention`;
  if (count === 0) {
    const empty = node("div", "source-panel");
    empty.append(node("p", "muted", "No analysis drafts are waiting for review."));
    elements.queueList.append(empty);
    return;
  }

  state.queue.forEach((item) => {
    const button = node("button", "queue-item");
    button.type = "button";
    if (state.currentBriefId === item.brief_id) button.classList.add("active");
    button.setAttribute("aria-label", `Review ${item.project_name}`);
    button.append(node("span", "queue-item-title", item.project_name));
    const meta = node("span", "queue-meta");
    meta.append(badge(humanize(item.review_status), item.review_status.replace("_", "-")));
    if (item.proposed_task_count === 0) {
      meta.append(badge("No proposed tasks", "zero"));
    } else {
      const label = item.proposed_task_count === 1 ? "1 task" : `${item.proposed_task_count} tasks`;
      meta.append(badge(label));
    }
    button.append(meta);
    button.addEventListener("click", () => openReview(item));
    elements.queueList.append(button);
  });
}

async function openReview(item) {
  state.currentBriefId = item.brief_id;
  renderQueue();
  showStatus(`Loading ${item.project_name}…`);
  try {
    const response = item.review_status === "ready"
      ? await apiRequest(`/api/v1/briefs/${item.brief_id}/review`, { method: "POST" })
      : await apiRequest(`/api/v1/briefs/${item.brief_id}/review`);
    state.current = response;
    setBriefUrl(item.brief_id);
    const queued = state.queue.find((entry) => entry.brief_id === item.brief_id);
    if (queued) {
      queued.review_status = "in_review";
      queued.review_id = response.id;
      queued.review_revision = response.revision;
    }
    renderQueue();
    renderReview();
    clearStatus();
  } catch (error) {
    handleError(error, "Unable to open this review.");
  }
}

async function refreshCurrentReview() {
  if (!state.currentBriefId) return;
  showStatus("Refreshing review…");
  try {
    state.current = await apiRequest(`/api/v1/briefs/${state.currentBriefId}/review`);
    renderReview();
    clearStatus();
  } catch (error) {
    handleError(error, "Unable to refresh this review.");
  }
}

function sectionHeader(title, context) {
  const wrapper = node("div", "section-title");
  wrapper.append(node("h3", "", title));
  if (context) wrapper.append(node("span", "section-kicker", context));
  return wrapper;
}

function sourcePanel(label, value) {
  const panel = node("div", "source-panel");
  panel.append(node("span", "source-label", label));
  panel.append(node("p", "", value));
  return panel;
}

function renderReview() {
  const review = state.current;
  if (!review) return;
  elements.emptyDetail.hidden = true;
  elements.reviewDetail.hidden = false;
  elements.reviewDetail.replaceChildren();

  const header = node("header", "review-header");
  const titleGroup = node("div");
  titleGroup.append(node("p", "eyebrow", "Client brief review"));
  titleGroup.append(node("h2", "", review.brief.project_name));
  titleGroup.append(node("p", "muted compact", `Client ${review.brief.client_id}`));
  const meta = node("div", "review-meta");
  meta.append(badge(humanize(review.status), review.status.replace("_", "-")));
  meta.append(badge(`Revision ${review.revision}`));
  header.append(titleGroup, meta);
  elements.reviewDetail.append(header);

  const briefSection = node("section", "section");
  briefSection.append(sectionHeader("Original client context", "Source material · read only"));
  briefSection.append(sourcePanel("Client brief", review.brief.message));
  if (review.brief.original_scope) {
    briefSection.append(sourcePanel("Original project scope", review.brief.original_scope));
  }
  elements.reviewDetail.append(briefSection);

  const analysisSection = node("section", "section");
  analysisSection.append(sectionHeader("AI analysis", "Validated draft · read only"));
  const analysisBand = node("div", "analysis-band");
  const requirements = node("ul", "requirement-list");
  review.analysis.requirements.forEach((requirement) => {
    const item = node("li", "requirement");
    item.append(node("span", "requirement-id", requirement.id));
    const text = node("div");
    text.append(node("p", "", requirement.description));
    text.append(node("p", "source-quote", `“${requirement.source_quote}”`));
    item.append(text);
    requirements.append(item);
  });
  if (review.analysis.requirements.length === 0) {
    requirements.append(node("li", "muted", "No requirements were extracted."));
  }
  analysisBand.append(requirements);
  const questions = node("ul", "clarification-list");
  review.analysis.clarification_questions.forEach((question) => {
    questions.append(node("li", "", question));
  });
  if (review.analysis.clarification_questions.length > 0) analysisBand.append(questions);
  analysisSection.append(analysisBand);
  elements.reviewDetail.append(analysisSection);

  const taskSection = node("section", "section");
  taskSection.append(sectionHeader("Human task review", review.status === "finalized" ? "Finalized · read only" : "Editable working copy"));
  const taskList = node("div", "task-list");
  if (review.tasks.length === 0) {
    taskList.append(sourcePanel("No proposed tasks", "The analysis produced no tasks. You can still finalize this empty review explicitly."));
  } else {
    review.tasks.forEach((task) => taskList.append(renderTask(task, review.status === "finalized")));
  }
  taskSection.append(taskList);
  elements.reviewDetail.append(taskSection);
  elements.reviewDetail.append(renderFinalizeBar());
}

function labeledControl(labelText, control, className = "") {
  const wrapper = node("div", `field ${className}`.trim());
  const id = `field-${crypto.randomUUID()}`;
  control.id = id;
  const label = node("label", "", labelText);
  label.htmlFor = id;
  wrapper.append(label, control);
  return wrapper;
}

function inputControl(value) {
  const input = document.createElement("input");
  input.type = "text";
  input.value = value;
  return input;
}

function textareaControl(value, rows = 4) {
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.rows = rows;
  return textarea;
}

function renderTask(task, readonly) {
  const card = node("article", `task-card ${task.decision}`);
  const heading = node("div", "task-heading");
  heading.append(node("h4", "", `Task ${task.position + 1} · ${humanize(task.type)}`));
  heading.append(badge(humanize(task.decision), task.decision));
  card.append(heading);

  if (readonly) {
    const grid = node("div", "task-grid");
    grid.append(readonlyField("Title", task.title));
    grid.append(readonlyField("Linked requirements", task.requirement_ids.join(", ")));
    grid.append(readonlyField("Description", task.description, "wide"));
    const criteria = node("div", "wide");
    criteria.append(node("span", "field-label", "Acceptance criteria"));
    const list = node("ul", "readonly-list");
    task.acceptance_criteria.forEach((criterion) => list.append(node("li", "", criterion)));
    criteria.append(list);
    grid.append(criteria);
    if (task.review_note) grid.append(readonlyField("Review note", task.review_note, "wide"));
    if (task.original_blocked_by_clarification) {
      grid.append(readonlyField("Clarification resolution", task.clarification_resolution || "Not resolved", "wide"));
    }
    const published = (task.publications || []).filter((item) => item.status === "published");
    if (published.length > 0) {
      const publicationField = node("div", "wide");
      publicationField.append(node("span", "field-label", "GitHub issue"));
      published.forEach((item) => {
        const link = node("a", "issue-link", `#${item.github_issue_number} · ${item.target_repository}`);
        link.href = item.github_issue_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        publicationField.append(link);
      });
      grid.append(publicationField);
    }
    card.append(grid);
    return card;
  }

  const grid = node("div", "task-grid");
  const title = inputControl(task.title);
  const description = textareaControl(task.description);
  const criteria = textareaControl(task.acceptance_criteria.join("\n"), 4);
  const note = textareaControl(task.review_note || "", 3);
  grid.append(labeledControl("Title", title));
  const linked = node("div", "field");
  linked.append(node("span", "field-label", "Linked requirement IDs"));
  const linkedIds = node("div", "linked-ids");
  task.requirement_ids.forEach((id) => linkedIds.append(badge(id, "in-review")));
  linked.append(linkedIds);
  grid.append(linked);
  grid.append(labeledControl("Description", description, "wide"));
  grid.append(labeledControl("Acceptance criteria (one per line)", criteria, "wide"));
  grid.append(labeledControl("Review note (optional)", note, "wide"));
  card.append(grid);

  let resolved = null;
  let resolution = null;
  if (task.original_blocked_by_clarification) {
    const warning = node("div", "warning-box");
    warning.append(node("strong", "", "Clarification required before approval"));
    warning.append(node("span", "", "Only mark this resolved after receiving a real client answer. ScopePilot will not infer one."));
    const checkboxRow = node("label", "checkbox-row");
    resolved = document.createElement("input");
    resolved.type = "checkbox";
    resolved.checked = task.clarification_resolved;
    checkboxRow.append(resolved, node("span", "", "Clarification has been resolved"));
    warning.append(checkboxRow);
    resolution = textareaControl(task.clarification_resolution || "", 3);
    warning.append(labeledControl("Resolution explanation", resolution));
    card.append(warning);
  }

  const actions = node("div", "task-actions");
  const save = node("button", "button secondary", "Save edits");
  save.type = "button";
  const approve = node("button", "button approve", "Approve");
  approve.type = "button";
  const reject = node("button", "button reject", "Reject");
  reject.type = "button";
  actions.append(save, approve, reject);
  card.append(actions);

  const canApprove = () => !task.original_blocked_by_clarification || (resolved.checked && resolution.value.trim().length > 0);
  const syncApproval = () => { approve.disabled = !canApprove(); };
  if (resolved && resolution) {
    resolved.addEventListener("change", syncApproval);
    resolution.addEventListener("input", syncApproval);
  }
  syncApproval();

  const submit = async (decision) => {
    const acceptanceCriteria = criteria.value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
    const payload = {
      review_revision: state.current.revision,
      task_revision: task.revision,
      title: title.value,
      description: description.value,
      acceptance_criteria: acceptanceCriteria,
      decision,
      review_note: note.value.trim() || null,
    };
    if (task.original_blocked_by_clarification) {
      payload.clarification_resolved = resolved.checked;
      payload.clarification_resolution = resolution.value.trim() || null;
    }
    save.disabled = true;
    approve.disabled = true;
    reject.disabled = true;
    showStatus("Saving task decision…");
    try {
      state.current = await apiRequest(
        `/api/v1/briefs/${state.currentBriefId}/review/tasks/${task.id}`,
        { method: "PATCH", body: JSON.stringify(payload) },
      );
      renderReview();
      renderQueue();
      showStatus("Task review saved.", "success");
    } catch (error) {
      handleError(error, "Unable to save this task.");
      save.disabled = false;
      reject.disabled = false;
      syncApproval();
    }
  };

  save.addEventListener("click", () => submit(task.decision));
  approve.addEventListener("click", () => submit("approved"));
  reject.addEventListener("click", () => submit("rejected"));
  return card;
}

function readonlyField(label, value, className = "") {
  const wrapper = node("div", className);
  wrapper.append(node("span", "field-label", label));
  wrapper.append(node("p", "readonly-value", value));
  return wrapper;
}

function renderFinalizeBar() {
  const review = state.current;
  const bar = node("section", "finalize-bar");
  const content = node("div");
  const counts = review.tasks.reduce(
    (result, task) => ({ ...result, [task.decision]: result[task.decision] + 1 }),
    { pending: 0, approved: 0, rejected: 0 },
  );
  content.append(node("h3", "", review.status === "finalized" ? "Review finalized" : "Finalize review"));
  content.append(node("p", "", review.status === "finalized"
    ? "Decisions and task edits are now locked. Nothing was published externally."
    : "Every task needs an explicit approval or rejection. Finalization does not publish to GitHub."));
  const countRow = node("div", "decision-counts");
  countRow.append(badge(`${counts.pending} pending`, "pending"));
  countRow.append(badge(`${counts.approved} approved`, "approved"));
  countRow.append(badge(`${counts.rejected} rejected`, "rejected"));
  content.append(countRow);
  bar.append(content);

  if (review.status !== "finalized") {
    const finalize = node("button", "button primary", "Finalize review");
    finalize.type = "button";
    finalize.disabled = counts.pending > 0;
    if (counts.pending > 0) finalize.title = "Resolve every pending task first";
    finalize.addEventListener("click", async () => {
      const confirmed = window.confirm("Finalize this review? Task edits and decisions will become read-only. Nothing will be published.");
      if (!confirmed) return;
      finalize.disabled = true;
      showStatus("Finalizing review…");
      try {
        state.current = await apiRequest(
          `/api/v1/briefs/${state.currentBriefId}/review/finalize`,
          { method: "POST", body: JSON.stringify({ review_revision: review.revision }) },
        );
        state.queue = state.queue.filter((item) => item.brief_id !== state.currentBriefId);
        renderQueue();
        renderReview();
        showStatus("Review finalized. No external publication occurred.", "success");
      } catch (error) {
        handleError(error, "Unable to finalize this review.");
        finalize.disabled = false;
      }
    });
    bar.append(finalize);
  } else if (counts.approved > 0) {
    const approved = review.tasks.filter((task) => task.decision === "approved");
    const unpublished = approved.filter(
      (task) => !(task.publications || []).some((item) => item.status === "published"),
    );
    const publish = node(
      "button",
      "button primary",
      unpublished.length > 0 ? "Publish approved tasks" : "Approved tasks published",
    );
    publish.type = "button";
    publish.disabled = unpublished.length === 0;
    if (unpublished.length === 0) publish.title = "All approved tasks already have GitHub issues";
    publish.addEventListener("click", async () => {
      const confirmed = window.confirm(
        `Publish ${unpublished.length} approved task${unpublished.length === 1 ? "" : "s"} as GitHub Issues?`,
      );
      if (!confirmed) return;
      publish.disabled = true;
      showStatus("Publishing approved tasks to GitHub…");
      try {
        const response = await apiRequest(
          `/api/v1/briefs/${state.currentBriefId}/review/publish`,
          { method: "POST" },
        );
        state.current = await apiRequest(`/api/v1/briefs/${state.currentBriefId}/review`);
        renderReview();
        renderPublicationResults(response);
        const publishedCount = response.results.filter(
          (item) => item.status === "published" || item.status === "already_published",
        ).length;
        showStatus(`${publishedCount} task publication${publishedCount === 1 ? "" : "s"} confirmed.`, "success");
      } catch (error) {
        handleError(error, "Unable to publish approved tasks.");
        publish.disabled = false;
      }
    });
    bar.append(publish);
  }
  return bar;
}

function renderPublicationResults(response) {
  const section = node("section", "section publication-results");
  section.append(sectionHeader("GitHub publication results", response.target_repository));
  const list = node("ul", "publication-list");
  response.results.forEach((result) => {
    const item = node("li", `publication-result ${result.status}`);
    item.append(node("strong", "", result.task_title));
    item.append(badge(humanize(result.status), result.status));
    if (result.issue_url) {
      const link = node("a", "issue-link", `Open issue #${result.issue_number}`);
      link.href = result.issue_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      item.append(link);
    }
    if (result.detail) item.append(node("span", "muted", result.detail));
    list.append(item);
  });
  if (response.results.length === 0) {
    list.append(node("li", "muted", "No approved tasks were eligible for publication."));
  }
  section.append(list);
  elements.reviewDetail.append(section);
}

elements.authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const key = elements.reviewKey.value.trim();
  if (!key) {
    showStatus("Enter the reviewer key to continue.", "error");
    return;
  }
  state.key = key;
  elements.reviewKey.value = "";
  await loadQueue();
});

elements.refreshQueue.addEventListener("click", loadQueue);
elements.refreshReview.addEventListener("click", refreshCurrentReview);
