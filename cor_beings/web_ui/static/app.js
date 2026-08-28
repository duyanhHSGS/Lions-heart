/*
TODO: Connect every data-todo control through an ordinary product Being.
TODO: Replace complete session refreshes with lifecycle-owned incremental events.
TODO: Add cancellation when AgentLoopBeing exposes a provider-neutral contract.
TODO: Persist threads only after SessionBeing gains an owned storage boundary.
TODO: Keep this script dependency-free and executable directly by the browser.
*/

"use strict";

const appShell = document.querySelector("#app-shell");
const thread = document.querySelector("#thread");
const form = document.querySelector("#chat-form");
const input = document.querySelector("#message");
const sendButton = document.querySelector("#send-button");
const messages = document.querySelector("#messages");
const messageScroll = document.querySelector("#message-scroll");
const recents = document.querySelector("#recents");
const toast = document.querySelector("#toast");
const themeToggle = document.querySelector("#theme-toggle");
const permissionLabel = document.querySelector("#permission-label");

let toastTimer = null;
let busy = false;

function showToast(text) {
  toast.textContent = text;
  toast.classList.add("visible");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove("visible"), 2800);
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
  const knownKind = ["user", "assistant", "tool_result", "tool_error", "agent_error"].includes(event.kind)
    ? event.kind
    : "tool_result";
  article.className = `message ${knownKind}`;
  const card = document.createElement("div");
  card.className = "message-card";
  card.textContent = textForEvent(event);
  article.append(card);
  return article;
}

function renderEvents(events) {
  messages.replaceChildren(...events.map(makeMessage));
  const hasMessages = events.length > 0;
  thread.classList.toggle("has-messages", hasMessages);
  recents.hidden = !hasMessages;
  if (hasMessages) {
    window.requestAnimationFrame(() => {
      messageScroll.scrollTop = messageScroll.scrollHeight;
    });
  }
}

async function loadSession() {
  try {
    const response = await fetch("/api/session", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`session request failed (${response.status})`);
    const payload = await response.json();
    renderEvents(Array.isArray(payload.events) ? payload.events : []);
  } catch (error) {
    showToast(`Session could not load · ${error.message}`);
  }
}

function resizeInput() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 190)}px`;
  sendButton.disabled = busy || input.value.trim().length === 0;
}

async function sendMessage(message) {
  busy = true;
  input.disabled = true;
  sendButton.disabled = true;
  closeMenus();
  try {
    const response = await fetch("/api/turn", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ message }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.kind ? `${payload.error}: ${payload.kind}` : payload.error);
    await loadSession();
  } catch (error) {
    showToast(`Turn failed · ${error.message}`);
    await loadSession();
  } finally {
    busy = false;
    input.disabled = false;
    resizeInput();
    input.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message || busy) return;
  input.value = "";
  resizeInput();
  void sendMessage(message);
});

input.addEventListener("input", resizeInput);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.querySelectorAll(".menu-trigger[data-menu]").forEach((trigger) => {
  trigger.addEventListener("click", (event) => {
    event.stopPropagation();
    const menuId = trigger.dataset.menu;
    const menu = document.getElementById(menuId);
    const opening = menu.hidden;
    closeMenus(opening ? menuId : null);
    menu.hidden = !opening;
    trigger.setAttribute("aria-expanded", String(opening));
  });
});

document.querySelectorAll(".popup").forEach((popup) => {
  popup.addEventListener("click", (event) => event.stopPropagation());
});

document.querySelectorAll("[data-todo]").forEach((control) => {
  control.addEventListener("click", () => {
    closeMenus();
    showToast(`TODO · ${control.dataset.todo}`);
  });
});

document.querySelectorAll("[data-permission]").forEach((control) => {
  control.addEventListener("click", () => {
    permissionLabel.textContent = control.dataset.permission;
    closeMenus();
    showToast(`Visual only · ${control.dataset.permission} needs a permission Being`);
  });
});

document.querySelector("#sidebar-toggle").addEventListener("click", () => {
  appShell.classList.toggle("sidebar-collapsed");
  closeMenus();
});

document.querySelector("#new-chat").addEventListener("click", () => {
  closeMenus();
  input.focus();
  showToast("TODO · New session needs a SessionBeing reset contract");
});

function applyTheme(dark) {
  document.documentElement.classList.toggle("dark", dark);
  themeToggle.textContent = dark ? "Light Mode" : "Dark Mode";
  try {
    window.localStorage.setItem("lions-heart-theme", dark ? "dark" : "light");
  } catch (_error) {
    // Local storage can be blocked; the visible theme still works for this page.
  }
}

themeToggle.addEventListener("click", () => {
  applyTheme(!document.documentElement.classList.contains("dark"));
  closeMenus();
});

document.addEventListener("click", () => closeMenus());
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeMenus();
});

try {
  applyTheme(window.localStorage.getItem("lions-heart-theme") === "dark");
} catch (_error) {
  applyTheme(false);
}
resizeInput();
void loadSession();
