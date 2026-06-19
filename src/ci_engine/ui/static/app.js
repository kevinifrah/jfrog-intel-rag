const state = {
  reports: [],
  selected: null,
};

const reportSelect = document.querySelector("#report-select");
const reportSelectHelp = document.querySelector("#report-select-help");
const selectedReportCard = document.querySelector("#selected-report-card");
const reportFrame = document.querySelector("#report-frame");
const selectedTitle = document.querySelector("#selected-title");
const selectedStatus = document.querySelector("#selected-status");
const pdfLink = document.querySelector("#pdf-link");
const pdfBlocker = document.querySelector("#pdf-blocker");
const chatLog = document.querySelector("#chat-log");
const chatForm = document.querySelector("#chat-form");
const chatInput = document.querySelector("#chat-input");

document.querySelector("#refresh-reports").addEventListener("click", loadReports);
reportSelect.addEventListener("change", () => selectReport(reportSelect.value));
chatForm.addEventListener("submit", submitChat);

loadReports();

async function loadReports() {
  const previousSlug = state.selected?.slug || reportSelect.value;
  reportSelect.disabled = true;
  reportSelect.innerHTML = '<option value="">Loading competitors...</option>';
  selectedReportCard.textContent = "";
  const response = await fetch("/api/reports");
  const payload = await response.json();
  state.reports = payload.reports || [];
  renderReportSelect();
  const preferred = state.reports.find((report) => report.slug === previousSlug) || state.reports[0];
  if (preferred) {
    selectReport(preferred.slug);
  }
}

function renderReportSelect() {
  reportSelect.innerHTML = "";
  if (!state.reports.length) {
    reportSelect.disabled = true;
    reportSelect.innerHTML = '<option value="">No competitor dossiers found</option>';
    reportSelectHelp.textContent = "No generated reports are available yet.";
    return;
  }
  reportSelect.disabled = false;
  reportSelectHelp.textContent = `${state.reports.length} competitor dossiers available. Validated reports appear first.`;
  appendReportGroup("Validated", state.reports.filter((report) => report.validation_passed));
  appendReportGroup("Review drafts", state.reports.filter((report) => !report.validation_passed));
}

function appendReportGroup(label, reports) {
  if (!reports.length) return;
  const group = document.createElement("optgroup");
  group.label = label;
  for (const report of reports) {
    const option = document.createElement("option");
    option.value = report.slug;
    option.textContent = optionLabel(report);
    group.appendChild(option);
  }
  reportSelect.appendChild(group);
}

function optionLabel(report) {
  const status = report.validation_passed ? "Validated" : "Needs review";
  const pdf = report.pdf_available ? "PDF ready" : "PDF not ready";
  return `${report.competitor || report.title || report.slug} - ${status} - ${pdf}`;
}

function selectReport(slug) {
  const report = state.reports.find((item) => item.slug === slug);
  if (!report) return;
  state.selected = report;
  reportSelect.value = report.slug;
  selectedTitle.textContent = report.title || `JFrog vs ${report.competitor}`;
  selectedStatus.textContent = `${report.executive_status_label || "Review draft available"} · ${formatDate(report.generated_at)}`;
  reportFrame.src = `/reports/${report.slug}/html?viewer=1&t=${Date.now()}`;
  renderSelectedReportCard(report);

  if (report.pdf_available) {
    pdfLink.classList.remove("disabled");
    pdfLink.href = `/reports/${report.slug}/pdf`;
    pdfLink.setAttribute("aria-disabled", "false");
    pdfLink.textContent = report.pdf_label || "Download PDF";
    pdfBlocker.classList.add("hidden");
    pdfBlocker.textContent = "";
  } else {
    pdfLink.classList.add("disabled");
    pdfLink.href = "#";
    pdfLink.setAttribute("aria-disabled", "true");
    pdfLink.textContent = report.pdf_label || "PDF not ready yet";
    pdfBlocker.classList.remove("hidden");
    pdfBlocker.innerHTML = "";
    const summary = document.createElement("strong");
    summary.textContent = report.readiness_summary || "The report is available for review, but the PDF is not ready yet.";
    const detail = document.createElement("p");
    detail.textContent = report.readiness_detail || "";
    const action = document.createElement("p");
    action.className = "next-action";
    action.textContent = report.recommended_action || "";
    pdfBlocker.append(summary, detail, action);
  }
}

function renderSelectedReportCard(report) {
  selectedReportCard.innerHTML = "";
  const title = document.createElement("strong");
  title.textContent = report.competitor || report.title || report.slug;
  const meta = document.createElement("div");
  meta.className = "row-meta";
  meta.appendChild(statusPill(report.validation_passed ? "Validated" : "Needs review", report.validation_passed ? "ready" : "review"));
  meta.appendChild(statusPill(report.pdf_available ? "PDF ready" : report.pdf_label || "PDF not ready yet", report.pdf_available ? "ready" : "review"));
  const date = document.createElement("span");
  date.textContent = formatDate(report.generated_at);
  meta.appendChild(date);
  const summary = document.createElement("p");
  summary.textContent = report.readiness_summary || "";
  selectedReportCard.append(title, meta, summary);
}

async function submitChat(event) {
  event.preventDefault();
  const question = chatInput.value.trim();
  if (!question) return;
  appendMessage("user", question);
  chatInput.value = "";
  const waiting = appendMessage("assistant", "Retrieving evidence...");
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      competitor: state.selected?.competitor || null,
      report_slug: state.selected?.slug || null,
      include_web: true,
      max_evidence: 8,
    }),
  });
  const answer = await response.json();
  waiting.remove();
  appendAnswer(answer);
}

function appendAnswer(answer) {
  const node = appendMessage("assistant", answer.answer || "No answer returned.");
  const sources = answer.sources || [];
  if (!sources.length || !shouldShowSources(answer)) return;
  const list = document.createElement("div");
  list.className = "source-list";
  for (const source of sources) {
    const chip = document.createElement(source.url ? "a" : "span");
    chip.className = "source-chip";
    chip.textContent = source.title || source.company || source.source;
    if (source.url) {
      chip.href = source.url;
      chip.target = "_blank";
      chip.rel = "noreferrer";
    }
    list.appendChild(chip);
  }
  node.querySelector(".bubble").appendChild(list);
}

function shouldShowSources(answer) {
  const metadata = answer.metadata || {};
  const web = metadata.web || {};
  const text = (answer.answer || "").toLowerCase();
  if (web.status === "ok" && (web.result_count || 0) > 0) return true;
  if ((answer.confidence || "") === "low" || (answer.confidence || "") === "unknown") return true;
  if (text.includes("not enough evidence") || text.includes("contradict")) return true;
  return false;
}

function appendMessage(role, text) {
  const wrapper = document.createElement("div");
  wrapper.className = `message ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrapper.appendChild(bubble);
  chatLog.appendChild(wrapper);
  chatLog.scrollTop = chatLog.scrollHeight;
  return wrapper;
}

function statusPill(text, tone) {
  const span = document.createElement("span");
  span.className = `status-pill ${tone || ""}`;
  span.textContent = text;
  return span;
}

function formatDate(value) {
  if (!value) return "No date";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
