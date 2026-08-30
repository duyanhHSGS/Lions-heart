/*
TODO: Add a richer non-blocking approval drawer after the safe confirm-based gate proves itself.
TODO: Add math and Mermaid only after a safe build-free renderer contract exists.
TODO: Keep Web Search disabled until its provider/security contract is chosen.
*/

"use strict";

import { renderMarkdownSubset } from "./markdown.js";

const $ = (selector) => document.querySelector(selector);
const appShell = $("#app-shell");
const thread = $("#thread");
const form = $("#chat-form");
const input = $("#message");
const sendButton = $("#send-button");
const messages = $("#messages");
const messageScroll = $("#message-scroll");
const recents = $("#recents");
const toast = $("#toast");
const themeToggle = $("#theme-toggle");
const permissionLabel = $("#permission-label");
const authScreen = $("#auth-screen");
const settingsModal = $("#settings-modal");
const settingsForm = $("#settings-form");
const filePicker = $("#file-picker");
const attachmentChips = $("#attachment-chips");
const savedPromptsModal = $("#saved-prompts-modal");

let toastTimer = null;
let busy = false;
let csrfToken = window.sessionStorage.getItem("lion-csrf") || "";
let settingsSnapshot = null;
let currentConversationId = "";
let activeTurnId = "";

function showToast(text) {
  toast.textContent = text;
  toast.classList.add("visible");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove("visible"), 3200);
}

async function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (options.method && options.method !== "GET" && csrfToken) headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(path, { ...options, headers });
  let payload = {};
  try { payload = await response.json(); }
  catch (_error) { payload = { error: `request failed (${response.status})` }; }
  if (!response.ok) {
    const error = new Error(payload.kind ? `${payload.error}: ${payload.kind}` : payload.error || `request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

function closeMenus(exceptId = null) {
  document.querySelectorAll(".menu-trigger[data-menu]").forEach((trigger) => {
    const menuId = trigger.dataset.menu;
    if (menuId === exceptId) return;
    const menu = document.getElementById(menuId);
    if (menu) menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
  });
}

function textForEvent(event) {
  const data = event && typeof event.data === "object" && event.data ? event.data : {};
  if (typeof data.text === "string" && data.text) return data.text;
  if (event.kind === "tool_result") return `${data.name || "tool"}\n${String(data.result ?? "")}`;
  if (event.kind === "tool_error") return `${data.name || "tool"}: ${data.error || "Error"}\n${data.message || ""}`;
  if (event.kind === "agent_error") return `Agent stopped: ${data.error || "unknown error"}`;
  return JSON.stringify(data, null, 2);
}

function makeMessage(event) {
  const article = document.createElement("article");
  const knownKind = ["user", "assistant", "tool_result", "tool_error", "agent_error"].includes(event.kind) ? event.kind : "tool_result";
  article.className = `message ${knownKind}`;
  const card = document.createElement("div");
  card.className = "message-card";
  if (knownKind === "assistant") card.append(renderMarkdownSubset(textForEvent(event)));
  else card.textContent = textForEvent(event);
  article.append(card);
  return article;
}

function renderEvents(events) {
  messages.replaceChildren(...events.map(makeMessage));
  const hasMessages = events.length > 0;
  thread.classList.toggle("has-messages", hasMessages);
  if (hasMessages) window.requestAnimationFrame(() => { messageScroll.scrollTop = messageScroll.scrollHeight; });
}

async function loadSession() {
  try {
    const payload = await apiFetch("/api/session");
    currentConversationId = typeof payload.conversation_id === "string" ? payload.conversation_id : "";
    renderEvents(Array.isArray(payload.events) ? payload.events : []);
    await loadAttachments();
  } catch (error) { if (error.status !== 401) showToast(`Session could not load · ${error.message}`); }
}

async function loadAttachments() {
  if (!currentConversationId || !attachmentChips) return;
  const payload = await apiFetch(`/api/attachments?conversation_id=${encodeURIComponent(currentConversationId)}`);
  const rows = Array.isArray(payload.attachments) ? payload.attachments : [];
  attachmentChips.replaceChildren(...rows.map((item) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "attachment-chip";
    chip.textContent = `📎 ${item.file_name}`;
    chip.title = "Remove attachment";
    chip.addEventListener("click", async () => {
      await apiFetch(`/api/attachments/${encodeURIComponent(item.id)}`, { method: "DELETE" });
      await loadAttachments();
    });
    return chip;
  }));
}

async function uploadFiles(files) {
  for (const file of files) {
    const mime = file.type || (file.name.toLowerCase().endsWith(".md") ? "text/markdown" : "text/plain");
    await apiFetch("/api/attachments", {
      method: "POST",
      headers: {
        "Content-Type": mime,
        "X-File-Name": encodeURIComponent(file.name),
        "X-Conversation-Id": currentConversationId,
      },
      body: file,
    });
  }
  await loadAttachments();
  showToast("Files indexed · Lion can sniff the words now 🦁");
}

async function loadSavedPrompts(query = "") {
  const payload = await apiFetch(`/api/saved-prompts?q=${encodeURIComponent(query)}`);
  const list = $("#saved-prompts-list");
  list.replaceChildren(...(payload.prompts || []).map((prompt) => {
    const row = document.createElement("div"); row.className = "resource-row";
    const insert = document.createElement("button"); insert.type = "button"; insert.textContent = prompt.name;
    insert.title = "Insert into composer without sending";
    insert.addEventListener("click", () => { input.value = `${input.value}${input.value ? "\n" : ""}${prompt.body}`; resizeInput(); input.focus(); savedPromptsModal.hidden = true; });
    const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Delete";
    remove.addEventListener("click", async () => { await apiFetch(`/api/saved-prompts/${encodeURIComponent(prompt.id)}`, { method: "DELETE" }); await loadSavedPrompts($("#saved-prompts-search").value); });
    row.append(insert, remove); return row;
  }));
}

async function loadMcpConnections() {
  const payload = await apiFetch("/api/mcp/connections");
  $("#mcp-list").replaceChildren(...(payload.connections || []).map((connection) => {
    const row = document.createElement("div"); row.className = "resource-row";
    const label = document.createElement("span"); label.textContent = `${connection.name} · ${connection.health}`;
    const refresh = document.createElement("button"); refresh.type = "button"; refresh.textContent = "Refresh";
    refresh.addEventListener("click", async () => { await apiFetch(`/api/mcp/${encodeURIComponent(connection.id)}/refresh`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); await loadMcpConnections(); });
    const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Delete";
    remove.addEventListener("click", async () => { await apiFetch(`/api/mcp/connections/${encodeURIComponent(connection.id)}`, { method: "DELETE" }); await loadMcpConnections(); });
    row.append(label, refresh, remove); return row;
  }));
}

async function loadConversations() {
  const payload = await apiFetch("/api/conversations");
  const rows = Array.isArray(payload.conversations) ? payload.conversations : [];
  recents.hidden = rows.length === 0;
  recents.replaceChildren();
  if (!rows.length) return;
  const heading = document.createElement("h2");
  heading.textContent = "Recents";
  recents.append(heading);
  rows.slice(0, 20).forEach((conversation) => {
    const button = document.createElement("button");
    button.className = "nav-row current-chat";
    button.type = "button";
    button.textContent = conversation.title || "New chat";
    button.addEventListener("click", async () => {
      await apiFetch("/api/conversations/open", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: conversation.id }) });
      await loadSession();
    });
    recents.append(button);
  });
}

function resizeInput() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 190)}px`;
  sendButton.disabled = !busy && input.value.trim().length === 0;
  sendButton.setAttribute("aria-label", busy ? "Stop response" : "Send message");
}

function appendStreamingAssistant() {
  const article = makeMessage({ kind: "assistant", data: { text: "" } });
  messages.append(article);
  thread.classList.add("has-messages");
  return article.querySelector(".message-card");
}

async function answerApproval(turnId, approvalId) {
  const payload = await apiFetch(`/api/turns/${encodeURIComponent(turnId)}/approvals`);
  const approval = (payload.approvals || []).find((item) => item.id === approvalId);
  if (!approval) throw new Error("approval details disappeared");
  const details = `${approval.risk}\n\nTool: ${approval.tool}\nArguments:\n${JSON.stringify(approval.arguments, null, 2)}`;
  const approved = window.confirm(`${details}\n\nAllow exactly once?`);
  await apiFetch(`/api/approvals/${encodeURIComponent(approvalId)}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved }),
  });
}

async function consumeTurn(turnId) {
  let after = 0;
  let finished = false;
  let assistantCard = null;
  let assistantText = "";
  let paintQueued = false;
  const paint = () => {
    paintQueued = false;
    if (assistantCard) assistantCard.replaceChildren(renderMarkdownSubset(assistantText));
    messageScroll.scrollTop = messageScroll.scrollHeight;
  };
  while (!finished && activeTurnId === turnId) {
    const response = await fetch(`/api/turns/${encodeURIComponent(turnId)}/events?after=${after}`, {
      headers: { Accept: "text/event-stream" },
      credentials: "same-origin",
    });
    if (!response.ok || !response.body) throw new Error(`event stream failed (${response.status})`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (!finished) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() || "";
      for (const frame of frames) {
        if (!frame || frame.startsWith(":")) continue;
        let kind = "message";
        let data = {};
        for (const line of frame.split("\n")) {
          if (line.startsWith("id:")) after = Number.parseInt(line.slice(3).trim(), 10) || after;
          else if (line.startsWith("event:")) kind = line.slice(6).trim();
          else if (line.startsWith("data:")) data = JSON.parse(line.slice(5).trim());
        }
        if (kind === "text_delta") {
          if (!assistantCard) assistantCard = appendStreamingAssistant();
          assistantText += typeof data.text === "string" ? data.text : "";
          if (!paintQueued) { paintQueued = true; window.requestAnimationFrame(paint); }
        } else if (kind === "approval_required") {
          await answerApproval(turnId, data.approval_id);
        } else if (["turn_completed", "turn_cancelled", "turn_failed"].includes(kind)) {
          finished = true;
        } else if (kind === "normalized_error") {
          showToast(data.message || "Turn failed");
        }
      }
      if (done) break;
    }
  }
}

async function sendMessage(message) {
  busy = true;
  input.disabled = true;
  sendButton.disabled = false;
  sendButton.setAttribute("aria-label", "Stop response");
  closeMenus();
  try {
    if (!currentConversationId) await loadSession();
    const payload = await apiFetch(`/api/sessions/${encodeURIComponent(currentConversationId)}/turns`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    activeTurnId = payload.turn_id;
    await consumeTurn(activeTurnId);
  } catch (error) { showToast(`Turn failed · ${error.message}`); }
  finally {
    activeTurnId = "";
    await loadSession();
    await loadConversations();
    busy = false;
    input.disabled = false;
    resizeInput();
    input.focus();
  }
}

async function cancelActiveTurn() {
  if (!activeTurnId) return;
  await apiFetch(`/api/turns/${encodeURIComponent(activeTurnId)}`, { method: "DELETE" });
}

function applyTheme(theme) {
  const dark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
  themeToggle.textContent = dark ? "Light Mode" : "Dark Mode";
}

function showSettingsTab(name) {
  document.querySelectorAll("[data-settings-tab]").forEach((button) => button.classList.toggle("active", button.dataset.settingsTab === name));
  document.querySelectorAll("[data-settings-panel]").forEach((panel) => { panel.hidden = panel.dataset.settingsPanel !== name; });
}

function fillSettings(snapshot) {
  settingsSnapshot = snapshot;
  const values = snapshot.values || {};
  for (const [name, value] of Object.entries(values)) {
    const field = settingsForm.elements.namedItem(name);
    if (field) field.value = value;
  }
  $("#active-model-label").textContent = values.default_text_model || "Configure provider";
  applyTheme(values.theme || "system");
  const keyFields = $("#provider-key-fields");
  keyFields.replaceChildren();
  for (const provider of ["openai", "anthropic", "gemini"]) {
    const status = snapshot.providers && snapshot.providers[provider];
    const label = document.createElement("label");
    label.textContent = `${provider[0].toUpperCase()}${provider.slice(1)} API key ${status && status.configured ? `(saved ···${status.suffix})` : ""}`;
    const field = document.createElement("input");
    field.type = "password";
    field.autocomplete = "off";
    field.dataset.providerKey = provider;
    field.placeholder = status && status.configured ? "Leave blank to keep saved key" : "Paste key";
    label.append(field);
    keyFields.append(label);
  }
}

async function loadSettings() {
  const snapshot = await apiFetch("/api/settings");
  fillSettings(snapshot);
  return snapshot;
}

async function openSettings(tab = "general") {
  closeMenus();
  settingsModal.hidden = false;
  showSettingsTab(tab);
  try { await loadSettings(); }
  catch (error) { $("#settings-error").textContent = error.message; }
}

async function initialize() {
  const status = await apiFetch("/api/auth/status");
  if (typeof status.csrf_token === "string" && status.csrf_token) {
    csrfToken = status.csrf_token;
    window.sessionStorage.setItem("lion-csrf", csrfToken);
  }
  if (!status.authenticated) {
    authScreen.hidden = false;
    appShell.setAttribute("inert", "");
    $("#auth-copy").textContent = status.setup_required ? "Create the single owner account." : "Sign in to Lion.";
    $("#auth-submit").textContent = status.setup_required ? "Create owner" : "Sign in";
    $("#auth-password").autocomplete = status.setup_required ? "new-password" : "current-password";
    authScreen.dataset.mode = status.setup_required ? "setup" : "login";
    return;
  }
  authScreen.hidden = true;
  appShell.removeAttribute("inert");
  await Promise.all([loadSession(), loadConversations(), loadSettings()]);
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (busy) { void cancelActiveTurn(); return; }
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  resizeInput();
  void sendMessage(message);
});

input.addEventListener("input", resizeInput);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); form.requestSubmit(); }
  if (event.key === "Escape" && busy) { event.preventDefault(); void cancelActiveTurn(); }
});

document.querySelectorAll(".menu-trigger[data-menu]").forEach((trigger) => {
  trigger.addEventListener("click", (event) => {
    event.stopPropagation();
    const menu = document.getElementById(trigger.dataset.menu);
    const opening = menu.hidden;
    closeMenus(opening ? trigger.dataset.menu : null);
    menu.hidden = !opening;
    trigger.setAttribute("aria-expanded", String(opening));
  });
});

document.querySelectorAll(".popup").forEach((popup) => popup.addEventListener("click", (event) => event.stopPropagation()));
document.querySelectorAll("[data-todo]").forEach((control) => control.addEventListener("click", () => { closeMenus(); showToast(`TODO · ${control.dataset.todo}`); }));
document.querySelectorAll("[data-permission]").forEach((control) => control.addEventListener("click", () => { permissionLabel.textContent = "Ask for approval"; closeMenus(); showToast("Every tool call requires approval"); }));

$("#sidebar-toggle").addEventListener("click", () => { appShell.classList.toggle("sidebar-collapsed"); closeMenus(); });
$("#home-nav").addEventListener("click", () => input.focus());
$("#models-nav").addEventListener("click", () => void openSettings("models"));
$("#projects-nav").addEventListener("click", async () => {
  const payload = await apiFetch("/api/projects");
  const names = (payload.projects || []).map((project) => project.name).slice(0, 4);
  showToast(names.length ? `Projects: ${names.join(", ")}` : "No projects yet · create one in Settings");
  await openSettings("projects");
});
$("#saved-prompts-open").addEventListener("click", async () => { closeMenus(); savedPromptsModal.hidden = false; await loadSavedPrompts(); $("#saved-prompts-search").focus(); });
$("#saved-prompts-close").addEventListener("click", () => { savedPromptsModal.hidden = true; });
$("#saved-prompts-search").addEventListener("input", (event) => void loadSavedPrompts(event.target.value));
$("#saved-prompt-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const data = new FormData(event.currentTarget);
  await apiFetch("/api/saved-prompts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: data.get("name"), body: data.get("body") }) });
  event.currentTarget.reset(); await loadSavedPrompts();
});
$("#mcp-open").addEventListener("click", async () => { closeMenus(); await openSettings("tools"); await loadMcpConnections(); });
$("#mcp-add").addEventListener("click", async () => {
  const transport = $("#mcp-transport").value; const target = $("#mcp-target").value.trim();
  const config = transport === "http" ? { url: target } : { argv: target.split(/\s+/).filter(Boolean) };
  await apiFetch("/api/mcp/connections", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: $("#mcp-name").value, transport, config, credential: $("#mcp-credential").value || null }) });
  $("#mcp-credential").value = ""; await loadMcpConnections();
});
$("#settings-open").addEventListener("click", () => void openSettings());
$("#run-settings").addEventListener("click", () => void openSettings("models"));
$("#temporary-chat").addEventListener("click", async () => {
  const payload = await apiFetch("/api/conversations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: "Temporary chat", temporary: true }) });
  currentConversationId = payload.id;
  renderEvents([]);
  showToast("Temporary chat · erased on close or shutdown");
  input.focus();
});
$("#new-chat").addEventListener("click", async () => {
  const payload = await apiFetch("/api/conversations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: "New chat", temporary: false }) });
  currentConversationId = payload.id;
  renderEvents([]);
  await loadConversations();
  input.focus();
});

$("#add-files").addEventListener("click", () => { closeMenus(); filePicker.click(); });
$("#chat-with-files").addEventListener("click", () => { closeMenus(); filePicker.click(); });
filePicker.addEventListener("change", async () => {
  try { await uploadFiles(Array.from(filePicker.files || [])); }
  catch (error) { showToast(error.message); }
  finally { filePicker.value = ""; }
});

themeToggle.addEventListener("click", async () => {
  const theme = document.documentElement.classList.contains("dark") ? "light" : "dark";
  try {
    const payload = await apiFetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ changes: { theme } }) });
    settingsSnapshot.values = payload.values;
    applyTheme(theme);
  } catch (error) { showToast(error.message); }
  closeMenus();
});

$("#auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#auth-error").textContent = "";
  try {
    const path = authScreen.dataset.mode === "setup" ? "/api/auth/setup" : "/api/auth/login";
    const payload = await apiFetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: $("#auth-username").value, password: $("#auth-password").value }) });
    csrfToken = payload.csrf_token;
    window.sessionStorage.setItem("lion-csrf", csrfToken);
    await initialize();
  } catch (error) { $("#auth-error").textContent = error.message; }
});

$("#logout").addEventListener("click", async () => {
  await apiFetch("/api/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  csrfToken = "";
  window.sessionStorage.removeItem("lion-csrf");
  window.location.reload();
});

document.querySelectorAll("[data-settings-tab]").forEach((button) => button.addEventListener("click", () => showSettingsTab(button.dataset.settingsTab)));
$("#settings-close").addEventListener("click", () => { settingsModal.hidden = true; });
settingsModal.addEventListener("click", (event) => { if (event.target === settingsModal) settingsModal.hidden = true; });

settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#settings-error").textContent = "";
  const data = new FormData(settingsForm);
  const changes = {};
  for (const name of ["default_provider", "default_text_model", "default_image_model", "default_video_model", "default_speech_model", "default_transcription_model", "system_prompt", "theme"]) changes[name] = String(data.get(name) || "");
  changes.retention_days = Number(data.get("retention_days"));
  try {
    await apiFetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ changes }) });
    for (const field of document.querySelectorAll("[data-provider-key]")) {
      if (!field.value) continue;
      await apiFetch("/api/provider-key", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: field.dataset.providerKey, secret: field.value }) });
      field.value = "";
    }
    await loadSettings();
    showToast("Settings saved");
  } catch (error) { $("#settings-error").textContent = error.message; }
});

$("#discover-models").addEventListener("click", async () => {
  const provider = settingsForm.elements.namedItem("default_provider").value;
  const results = $("#model-results");
  results.textContent = "Discovering…";
  try {
    const payload = await apiFetch(`/api/providers/${encodeURIComponent(provider)}/models`);
    results.replaceChildren();
    payload.models.slice(0, 100).forEach((model) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = model;
      button.addEventListener("click", () => { settingsForm.elements.namedItem("default_text_model").value = model; });
      results.append(button);
    });
  } catch (error) { results.textContent = error.message; }
});

document.addEventListener("click", () => closeMenus());
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") { closeMenus(); settingsModal.hidden = true; savedPromptsModal.hidden = true; }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); void openSettings(); }
});

resizeInput();
void initialize().catch((error) => showToast(`Lion could not start · ${error.message}`));
