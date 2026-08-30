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
const chatActionModal = $("#chat-action-modal");
const chatActionForm = $("#chat-action-form");

let toastTimer = null;
let busy = false;
let csrfToken = window.sessionStorage.getItem("lion-csrf") || "";
let settingsSnapshot = null;
let currentConversationId = "";
let activeTurnId = "";
let chatAction = null;
let conversationProjects = [];
let providerConnections = [];

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
  document.querySelectorAll(".chat-options-trigger").forEach((trigger) => {
    const menuId = trigger.getAttribute("aria-controls");
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
  const [payload, projectPayload] = await Promise.all([apiFetch("/api/conversations"), apiFetch("/api/projects")]);
  const rows = Array.isArray(payload.conversations) ? payload.conversations : [];
  conversationProjects = Array.isArray(projectPayload.projects) ? projectPayload.projects : [];
  recents.hidden = rows.length === 0;
  recents.replaceChildren();
  if (!rows.length) return;
  const pinned = rows.filter((item) => Boolean(item.pinned));
  const recent = rows.filter((item) => !item.pinned);
  if (pinned.length) recents.append(makeConversationGroup("Pinned", pinned.slice(0, 20)));
  if (recent.length) recents.append(makeConversationGroup("Recents", recent.slice(0, Math.max(0, 20 - pinned.length))));
}

function icon(name, className = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  if (className) svg.setAttribute("class", className);
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`); svg.append(use); return svg;
}

function chatMenuAction(label, iconName, action, className = "") {
  const button = document.createElement("button"); button.type = "button"; button.className = className;
  button.append(icon(iconName), document.createTextNode(label));
  button.addEventListener("click", (event) => {
    event.stopPropagation(); closeMenus();
    void Promise.resolve(action()).catch((error) => showToast(`Chat update failed · ${error.message}`));
  });
  return button;
}

function makeConversationGroup(label, conversations) {
  const group = document.createElement("section"); group.className = "chat-group";
  const heading = document.createElement("h2"); heading.textContent = label; group.append(heading);
  for (const conversation of conversations) group.append(makeConversationRow(conversation));
  return group;
}

function makeConversationRow(conversation) {
  const row = document.createElement("div"); row.className = "chat-row";
  if (conversation.id === currentConversationId) row.classList.add("active");
  const open = document.createElement("button"); open.className = "nav-row current-chat"; open.type = "button";
  const titleWrap = document.createElement("span"); titleWrap.className = "chat-title-wrap";
  if (conversation.pinned) titleWrap.append(icon("pin", "chat-pin"));
  const title = document.createElement("span"); title.className = "history-title"; title.textContent = conversation.title || "New chat";
  titleWrap.append(title); open.append(titleWrap);
  open.addEventListener("click", async () => {
    if (busy) { showToast("Finish or stop the current answer before switching chats"); return; }
    await apiFetch("/api/conversations/open", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: conversation.id }) });
    await loadSession(); await loadConversations(); input.focus();
  });

  const menuId = `chat-menu-${conversation.id}`;
  const trigger = document.createElement("button"); trigger.type = "button"; trigger.className = "chat-options-trigger";
  trigger.setAttribute("aria-label", `Options for ${conversation.title || "New chat"}`); trigger.setAttribute("aria-expanded", "false"); trigger.setAttribute("aria-controls", menuId); trigger.append(icon("more"));
  const menu = document.createElement("div"); menu.className = "popup chat-options-menu"; menu.id = menuId; menu.hidden = true;
  menu.append(
    chatMenuAction("Rename", "pencil", () => openChatAction("rename", conversation)),
    chatMenuAction(conversation.pinned ? "Unpin chat" : "Pin chat", "pin", async () => {
      await updateConversation(conversation.id, { pinned: !Boolean(conversation.pinned) });
      showToast(conversation.pinned ? "Chat unpinned" : "Chat pinned up top");
    }),
    chatMenuAction("Move to project", "folder", () => openChatAction("move", conversation)),
  );
  const markdown = document.createElement("a"); markdown.href = `/api/conversations/${encodeURIComponent(conversation.id)}/export?format=markdown`; markdown.append(icon("download"), document.createTextNode("Export Markdown"));
  const json = document.createElement("a"); json.href = `/api/conversations/${encodeURIComponent(conversation.id)}/export?format=json`; json.append(icon("download"), document.createTextNode("Export JSON"));
  const divider = document.createElement("hr");
  menu.append(markdown, json, divider,
    chatMenuAction("Archive", "archive", () => openChatAction("archive", conversation)),
    chatMenuAction("Delete forever", "trash", () => openChatAction("delete", conversation), "danger"),
  );
  trigger.addEventListener("click", (event) => {
    event.stopPropagation(); const opening = menu.hidden; closeMenus(opening ? menuId : null); menu.hidden = !opening; trigger.setAttribute("aria-expanded", String(opening));
    if (opening) {
      const rect = trigger.getBoundingClientRect();
      menu.style.position = "fixed";
      menu.style.left = `${Math.max(8, Math.min(rect.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 8))}px`;
      menu.style.top = `${Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - menu.offsetHeight - 8))}px`;
    }
  });
  menu.addEventListener("click", (event) => event.stopPropagation());
  row.append(open, trigger, menu); return row;
}

async function updateConversation(conversationId, change) {
  await apiFetch(`/api/conversations/${encodeURIComponent(conversationId)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(change) });
  await loadConversations();
}

function closeChatAction() {
  chatActionModal.hidden = true; chatAction = null; chatActionForm.reset(); $("#chat-action-error").textContent = "";
}

function openChatAction(kind, conversation) {
  closeMenus(); chatAction = { kind, conversation }; chatActionModal.hidden = false;
  const titles = { rename: "Rename chat", move: "Move to project", archive: "Archive chat?", delete: "Delete chat forever?" };
  const copies = { rename: "Give this chat a name that Future You can actually find.", move: "Choose a project home, or send it back to ordinary Recents.", archive: "The chat leaves the sidebar but stays safely stored.", delete: "This removes the chat and its messages. This cannot be undone." };
  $("#chat-action-title").textContent = titles[kind]; $("#chat-action-copy").textContent = copies[kind];
  $("#chat-title-field").hidden = kind !== "rename"; $("#chat-project-field").hidden = kind !== "move";
  const titleInput = $("#chat-title-input"); titleInput.required = kind === "rename"; titleInput.value = conversation.title || "New chat";
  const select = $("#chat-project-select"); select.replaceChildren();
  const recentsOption = document.createElement("option"); recentsOption.value = ""; recentsOption.textContent = "Recents (no project)"; select.append(recentsOption);
  const newOption = document.createElement("option"); newOption.value = "__new__"; newOption.textContent = "+ New project"; select.append(newOption);
  for (const project of conversationProjects) { const option = document.createElement("option"); option.value = project.id; option.textContent = project.name; select.append(option); }
  select.value = conversation.project_id || "";
  $("#chat-new-project-field").hidden = true; $("#chat-new-project-input").required = false;
  const submit = $("#chat-action-submit"); submit.textContent = kind === "rename" ? "Save name" : kind === "move" ? "Move chat" : kind === "archive" ? "Archive" : "Delete forever";
  submit.classList.toggle("danger-action", kind === "delete");
  window.requestAnimationFrame(() => (kind === "rename" ? titleInput : kind === "move" ? select : submit).focus());
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
  const drawer = $("#approval-drawer");
  $("#approval-details").textContent = details;
  drawer.hidden = false;
  const approved = await new Promise((resolve) => {
    $("#approval-allow").onclick = () => resolve(true);
    $("#approval-reject").onclick = () => resolve(false);
  });
  drawer.hidden = true;
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

function providerModelList(value) {
  return String(value || "").split(",").map((item) => item.trim()).filter(Boolean);
}

function providerConnectionCard(connection) {
  const card = document.createElement("article"); card.className = "provider-card"; card.dataset.providerId = connection.id;
  const heading = document.createElement("div"); heading.className = "provider-card-heading";
  const title = document.createElement("strong"); title.textContent = connection.display_name;
  const badge = document.createElement("span"); badge.textContent = connection.enabled ? "Enabled" : "Disabled";
  heading.append(title, badge); card.append(heading);
  const name = document.createElement("input"); name.value = connection.display_name; name.maxLength = 80; name.setAttribute("aria-label", "Provider name");
  const url = document.createElement("input"); url.value = connection.base_url; url.maxLength = 2048; url.setAttribute("aria-label", "Provider base URL");
  const models = document.createElement("input"); models.value = (connection.models || []).join(", "); models.maxLength = 4000; models.setAttribute("aria-label", "Provider model IDs");
  const secret = document.createElement("input"); secret.type = "password"; secret.autocomplete = "off"; secret.placeholder = connection.configured ? "Leave blank to keep saved key" : "API key"; secret.setAttribute("aria-label", "Replacement API key");
  const actions = document.createElement("div"); actions.className = "provider-card-actions";
  const save = document.createElement("button"); save.type = "button"; save.textContent = "Save connection";
  const toggle = document.createElement("button"); toggle.type = "button"; toggle.textContent = connection.enabled ? "Disable" : "Enable";
  const clearKey = document.createElement("button"); clearKey.type = "button"; clearKey.textContent = "Clear key"; clearKey.hidden = !connection.configured;
  const remove = document.createElement("button"); remove.type = "button"; remove.className = "danger"; remove.textContent = "Delete";
  save.addEventListener("click", async () => {
    const body = { display_name: name.value, base_url: url.value, models: providerModelList(models.value), revision: connection.revision };
    if (secret.value) body.secret = secret.value;
    await apiFetch(`/api/providers/${encodeURIComponent(connection.id)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    await loadSettings(); showToast("Provider connection saved");
  });
  toggle.addEventListener("click", async () => {
    await apiFetch(`/api/providers/${encodeURIComponent(connection.id)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: !connection.enabled, revision: connection.revision }) });
    await loadSettings();
  });
  clearKey.addEventListener("click", async () => {
    await apiFetch(`/api/providers/${encodeURIComponent(connection.id)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ clear_secret: true, revision: connection.revision }) });
    await loadSettings(); showToast("Provider key cleared");
  });
  remove.addEventListener("click", async () => {
    if (remove.dataset.confirm !== "yes") {
      remove.dataset.confirm = "yes"; remove.textContent = "Delete forever?";
      window.setTimeout(() => { remove.dataset.confirm = ""; remove.textContent = "Delete"; }, 5000);
      return;
    }
    await apiFetch(`/api/providers/${encodeURIComponent(connection.id)}`, { method: "DELETE" });
    await loadSettings(); showToast("Provider connection deleted");
  });
  actions.append(save, toggle, clearKey, remove); card.append(name, url, models, secret, actions); return card;
}

function fillSettings(snapshot, connections = providerConnections) {
  settingsSnapshot = snapshot;
  const values = snapshot.values || {};
  providerConnections = connections;
  const providerSelect = $("#default-provider");
  providerSelect.replaceChildren();
  for (const connection of connections.filter((item) => item.enabled)) {
    const option = document.createElement("option"); option.value = connection.id; option.textContent = connection.display_name; providerSelect.append(option);
  }
  for (const [name, value] of Object.entries(values)) {
    const field = settingsForm.elements.namedItem(name);
    if (field) field.value = value;
  }
  $("#active-model-label").textContent = values.default_text_model || "Configure provider";
  applyTheme(values.theme || "system");
  const keyFields = $("#provider-key-fields");
  keyFields.replaceChildren();
  for (const provider of connections.filter((item) => item.built_in).map((item) => item.id)) {
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
  const custom = $("#provider-connections"); custom.replaceChildren();
  for (const connection of connections.filter((item) => !item.built_in)) custom.append(providerConnectionCard(connection));
}

async function loadSettings() {
  const [snapshot, catalog] = await Promise.all([apiFetch("/api/settings"), apiFetch("/api/providers")]);
  fillSettings(snapshot, Array.isArray(catalog.providers) ? catalog.providers : []);
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
$("#provider-add").addEventListener("click", async () => {
  const body = {
    display_name: $("#provider-name").value,
    base_url: $("#provider-base-url").value,
    models: providerModelList($("#provider-models").value),
    secret: $("#provider-secret").value || null,
  };
  try {
    await apiFetch("/api/providers", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    for (const id of ["#provider-name", "#provider-base-url", "#provider-models", "#provider-secret"]) $(id).value = "";
    await loadSettings(); showToast("Generic provider added");
  } catch (error) { $("#settings-error").textContent = error.message; }
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

let workbenchKind = "image";
const workbenchModal = $("#workbench-modal");
const workbenchList = $("#workbench-list");
const mediaForm = $("#media-form");
const recipeForm = $("#recipe-form");

function resourceRow(label, actions = []) {
  const row = document.createElement("div"); row.className = "resource-row";
  const text = document.createElement("span"); text.textContent = label; row.append(text, ...actions); return row;
}

async function loadWorkbench() {
  workbenchList.textContent = "Loading…";
  if (workbenchKind === "activity") {
    const [items, totals] = await Promise.all([apiFetch("/api/activity"), apiFetch("/api/activity/totals")]);
    const rows = [resourceRow(`${totals.requests} requests · ${totals.input_tokens + totals.output_tokens} tokens`)];
    for (const item of items.activity || []) rows.push(resourceRow(`${item.provider} / ${item.model} · ${item.capability} · ${item.status}`));
    workbenchList.replaceChildren(...rows); return;
  }
  if (workbenchKind === "recipes") {
    const payload = await apiFetch("/api/recipes");
    workbenchList.replaceChildren(...(payload.recipes || []).map((item) => {
      const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Delete";
      remove.addEventListener("click", async () => { await apiFetch(`/api/recipes/${encodeURIComponent(item.id)}`, { method: "DELETE" }); await loadWorkbench(); });
      return resourceRow(`${item.name} · revision ${item.revision}`, [remove]);
    })); return;
  }
  const payload = await apiFetch(`/api/media?kind=${encodeURIComponent(workbenchKind)}`);
  workbenchList.replaceChildren(...(payload.jobs || []).map((item) => {
    const actions = [];
    if (!["completed", "failed", "cancelled"].includes(item.status)) {
      const cancel = document.createElement("button"); cancel.type = "button"; cancel.textContent = "Cancel";
      cancel.addEventListener("click", async () => { await apiFetch(`/api/media/${encodeURIComponent(item.id)}/cancel`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); await loadWorkbench(); }); actions.push(cancel);
    }
    if (item.status === "completed") {
      const download = document.createElement("a"); download.href = `/api/media/${encodeURIComponent(item.id)}/content`; download.textContent = "Download"; actions.push(download);
    }
    if (["completed", "failed", "cancelled"].includes(item.status)) {
      const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Delete";
      remove.addEventListener("click", async () => { await apiFetch(`/api/media/${encodeURIComponent(item.id)}`, { method: "DELETE" }); await loadWorkbench(); }); actions.push(remove);
    }
    return resourceRow(`${item.model} · ${item.status} · ${item.progress}%`, actions);
  }));
}

async function openWorkbench(kind) {
  closeMenus(); workbenchKind = kind; workbenchModal.hidden = false;
  $("#workbench-title").textContent = kind === "activity" ? "API Activity" : kind[0].toUpperCase() + kind.slice(1);
  mediaForm.hidden = ["recipes", "activity"].includes(kind); recipeForm.hidden = kind !== "recipes";
  try { await loadWorkbench(); } catch (error) { workbenchList.textContent = error.message; }
}

for (const [id, kind] of [["#images-nav", "image"], ["#video-nav", "video"], ["#audio-nav", "audio"], ["#recipes-nav", "recipes"], ["#activity-nav", "activity"]]) {
  $(id).addEventListener("click", () => void openWorkbench(kind));
}
$("#workbench-close").addEventListener("click", () => { workbenchModal.hidden = true; });
workbenchModal.addEventListener("click", (event) => { if (event.target === workbenchModal) workbenchModal.hidden = true; });
$("#chat-action-close").addEventListener("click", closeChatAction);
$("#chat-action-cancel").addEventListener("click", closeChatAction);
$("#chat-project-select").addEventListener("change", (event) => {
  const creating = event.target.value === "__new__";
  $("#chat-new-project-field").hidden = !creating; $("#chat-new-project-input").required = creating;
  if (creating) $("#chat-new-project-input").focus();
});
chatActionModal.addEventListener("click", (event) => { if (event.target === chatActionModal) closeChatAction(); });
chatActionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!chatAction) return;
  const { kind, conversation } = chatAction;
  if (busy && conversation.id === currentConversationId && ["archive", "delete"].includes(kind)) {
    $("#chat-action-error").textContent = "Stop the current answer before removing this chat from view."; return;
  }
  $("#chat-action-error").textContent = ""; $("#chat-action-submit").disabled = true;
  try {
    if (kind === "rename") await updateConversation(conversation.id, { title: $("#chat-title-input").value.trim() });
    else if (kind === "move") {
      let projectId = $("#chat-project-select").value || null;
      if (projectId === "__new__") {
        const created = await apiFetch("/api/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: $("#chat-new-project-input").value.trim() }) });
        projectId = created.id;
      }
      await updateConversation(conversation.id, { project_id: projectId });
    }
    else if (kind === "archive") {
      await updateConversation(conversation.id, { archived: true });
      if (conversation.id === currentConversationId) {
        await apiFetch("/api/conversations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: "New chat", temporary: false }) });
        await loadSession(); await loadConversations();
      }
    } else if (kind === "delete") {
      await apiFetch(`/api/conversations/${encodeURIComponent(conversation.id)}`, { method: "DELETE" });
      await loadSession(); await loadConversations();
    }
    closeChatAction(); showToast(kind === "delete" ? "Chat deleted" : kind === "archive" ? "Chat archived" : kind === "move" ? "Chat moved" : "Chat renamed");
  } catch (error) { $("#chat-action-error").textContent = error.message; }
  finally { $("#chat-action-submit").disabled = false; }
});
mediaForm.addEventListener("submit", async (event) => {
  event.preventDefault(); const data = new FormData(mediaForm);
  const body = { prompt: data.get("prompt"), provider: data.get("provider"), model: data.get("model") };
  if (workbenchKind !== "image") body.duration_seconds = Number(data.get("duration_seconds"));
  try { await apiFetch(`/api/${workbenchKind === "image" ? "images" : workbenchKind}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); await loadWorkbench(); }
  catch (error) { showToast(error.message); }
});
recipeForm.addEventListener("submit", async (event) => {
  event.preventDefault(); const data = new FormData(recipeForm);
  try { await apiFetch("/api/recipes", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: data.get("name"), graph: JSON.parse(String(data.get("graph"))) }) }); await loadWorkbench(); }
  catch (error) { showToast(error.message); }
});

document.addEventListener("click", () => closeMenus());
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") { closeMenus(); settingsModal.hidden = true; savedPromptsModal.hidden = true; workbenchModal.hidden = true; closeChatAction(); }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); void openSettings(); }
});

resizeInput();
void initialize().catch((error) => showToast(`Lion could not start · ${error.message}`));
