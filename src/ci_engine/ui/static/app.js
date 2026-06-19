const state = {
  reports: [],
  selected: null,
};

const competitorIndex = document.querySelector("#competitor-index");
const reportFrame = document.querySelector("#report-frame");
const readerTitle = document.querySelector("#reader-title");
const readerStatus = document.querySelector("#reader-status");
const pdfLink = document.querySelector("#pdf-link");
const pdfBlocker = document.querySelector("#pdf-blocker");
const askOpen = document.querySelector("#ask-open");
const askClose = document.querySelector("#ask-close");
const chatClear = document.querySelector("#chat-clear");
const scrim = document.querySelector("#scrim");
const drawer = document.querySelector("#chat-drawer");
const chatLog = document.querySelector("#chat-log");
const chatForm = document.querySelector("#chat-form");
const chatInput = document.querySelector("#chat-input");

document.querySelector("#refresh-reports").addEventListener("click", loadReports);
askOpen.addEventListener("click", openDrawer);
askClose.addEventListener("click", closeDrawer);
chatClear.addEventListener("click", clearChat);
scrim.addEventListener("click", closeDrawer);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && drawer.classList.contains("open")) closeDrawer();
});
chatForm.addEventListener("submit", submitChat);

loadReports();

async function loadReports() {
  const previousSlug = state.selected?.slug || null;
  competitorIndex.innerHTML = '<li class="ledger-empty">Loading&hellip;</li>';
  const response = await fetch("/api/reports");
  const payload = await response.json();
  state.reports = payload.reports || [];
  renderIndex();
  const preferred = state.reports.find((report) => report.slug === previousSlug) || state.reports[0];
  if (preferred) selectReport(preferred.slug);
}

function renderIndex() {
  competitorIndex.innerHTML = "";
  if (!state.reports.length) {
    competitorIndex.innerHTML = '<li class="ledger-empty">No dossiers available yet.</li>';
    return;
  }
  for (const report of state.reports) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ledger-item";
    button.dataset.slug = report.slug;

    const name = document.createElement("span");
    name.className = "ledger-name";
    name.textContent = report.competitor || report.title || report.slug;
    button.appendChild(name);

    if (!report.validation_passed) {
      const flag = document.createElement("span");
      flag.className = "ledger-flag";
      flag.textContent = "in review";
      button.appendChild(flag);
    }

    button.addEventListener("click", () => selectReport(report.slug));
    item.appendChild(button);
    competitorIndex.appendChild(item);
  }
}

function markActive(slug) {
  for (const button of competitorIndex.querySelectorAll(".ledger-item")) {
    button.classList.toggle("active", button.dataset.slug === slug);
  }
}

function selectReport(slug) {
  const report = state.reports.find((item) => item.slug === slug);
  if (!report) return;
  state.selected = report;
  markActive(slug);

  readerTitle.textContent = report.title || `JFrog vs ${report.competitor}`;
  readerStatus.textContent = `${report.executive_status_label || "Draft available"} · ${formatDate(report.generated_at)}`;
  reportFrame.src = `/reports/${report.slug}/html?t=${Date.now()}`;

  if (report.pdf_available) {
    pdfLink.classList.remove("disabled");
    pdfLink.href = `/reports/${report.slug}/pdf`;
    pdfLink.setAttribute("aria-disabled", "false");
    pdfLink.textContent = "Download PDF";
    pdfBlocker.classList.add("hidden");
    pdfBlocker.textContent = "";
  } else {
    pdfLink.classList.add("disabled");
    pdfLink.href = "#";
    pdfLink.setAttribute("aria-disabled", "true");
    pdfLink.textContent = "PDF not ready";
    pdfBlocker.classList.remove("hidden");
    pdfBlocker.innerHTML = "";
    const summary = document.createElement("strong");
    summary.textContent = report.readiness_summary || "This dossier is available to read; the PDF is not ready yet.";
    const detail = document.createElement("p");
    detail.textContent = report.readiness_detail || "";
    const action = document.createElement("p");
    action.className = "next-action";
    action.textContent = report.recommended_action || "";
    pdfBlocker.append(summary, detail, action);
  }
}

function openDrawer() {
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  scrim.classList.remove("hidden");
  chatInput.focus();
}

function closeDrawer() {
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  scrim.classList.add("hidden");
}

function clearChat() {
  chatLog.innerHTML = "";
}

async function submitChat(event) {
  event.preventDefault();
  const question = chatInput.value.trim();
  if (!question) return;
  appendMessage("user", question);
  chatInput.value = "";
  const waiting = appendMessage("assistant", "Retrieving evidence…");
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
  if (role === "assistant") {
    bubble.innerHTML = renderMarkdown(text);
  } else {
    bubble.textContent = text;
  }
  wrapper.appendChild(bubble);
  chatLog.appendChild(wrapper);
  chatLog.scrollTop = chatLog.scrollHeight;
  return wrapper;
}

function renderMarkdown(text) {
  const escaped = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return escaped
    // Bold
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    // Italic
    .replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, "<em>$1</em>")
    // Bullet lists: consecutive lines starting with "- " or "* "
    .replace(/((?:^[-*] .+\n?)+)/gm, (block) => {
      const items = block.trim().split("\n").map((line) =>
        `<li>${line.replace(/^[-*] /, "")}</li>`
      ).join("");
      return `<ul>${items}</ul>`;
    })
    // Paragraphs: double newline → paragraph break
    .replace(/\n{2,}/g, "</p><p>")
    // Single newline inside a paragraph
    .replace(/\n/g, "<br>")
    .replace(/^/, "<p>")
    .replace(/$/, "</p>")
    // Clean up empty paragraphs wrapping block elements
    .replace(/<p>(<ul>)/g, "$1")
    .replace(/(<\/ul>)<\/p>/g, "$1");
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
