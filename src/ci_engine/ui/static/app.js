const state = {
  reports: [],
  selected: null,
};

const reportSelect = document.querySelector("#report-select");
const reportFrame = document.querySelector("#report-frame");
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
const chatSubmit = chatForm.querySelector('button[type="submit"]');
const CHAT_TIMEOUT_MS = 240000;

document.querySelector("#refresh-reports").addEventListener("click", loadReports);
reportSelect.addEventListener("change", () => selectReport(reportSelect.value));
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
  reportSelect.innerHTML = '<option value="">Loading&hellip;</option>';
  const response = await fetch("/api/reports");
  const payload = await response.json();
  state.reports = payload.reports || [];
  renderIndex();
  const preferred = state.reports.find((report) => report.slug === previousSlug) || state.reports[0];
  if (preferred) selectReport(preferred.slug);
}

function renderIndex() {
  reportSelect.innerHTML = "";
  if (!state.reports.length) {
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "No dossiers available yet";
    reportSelect.appendChild(empty);
    reportSelect.disabled = true;
    return;
  }
  reportSelect.disabled = false;
  for (const report of state.reports) {
    const option = document.createElement("option");
    option.value = report.slug;
    option.textContent = report.competitor || report.title || report.slug;
    reportSelect.appendChild(option);
  }
}

function selectReport(slug) {
  const report = state.reports.find((item) => item.slug === slug);
  if (!report) return;
  state.selected = report;
  reportSelect.value = slug;

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
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), CHAT_TIMEOUT_MS);
  chatSubmit.disabled = true;
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        question,
        competitor: state.selected?.competitor || null,
        report_slug: state.selected?.slug || null,
        include_web: true,
        max_evidence: 8,
      }),
    });
    const answer = await parseJsonResponse(response);
    if (!response.ok) {
      throw new Error(answer.detail || answer.message || `Chat request failed (${response.status}).`);
    }
    waiting.remove();
    appendAnswer(answer);
  } catch (error) {
    waiting.remove();
    appendMessage("assistant", chatErrorMessage(error));
  } finally {
    window.clearTimeout(timeout);
    chatSubmit.disabled = false;
    chatInput.focus();
  }
}

async function parseJsonResponse(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch (error) {
    if (!response.ok) {
      return { message: text.slice(0, 240) };
    }
    throw error;
  }
}

function chatErrorMessage(error) {
  if (error?.name === "AbortError") {
    return "The evidence search took too long and was stopped. Please try a narrower question or retry in a moment.";
  }
  return `I couldn't complete the evidence search. ${error?.message || "Please try again."}`;
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
