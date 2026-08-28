/* Lion-owned safe Markdown subset. Unsupported HTML stays harmless text. */

"use strict";

function appendInline(parent, source) {
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\))/g;
  let cursor = 0;
  for (const match of source.matchAll(pattern)) {
    parent.append(document.createTextNode(source.slice(cursor, match.index)));
    const token = match[0];
    if (token.startsWith("`")) {
      const code = document.createElement("code");
      code.textContent = token.slice(1, -1);
      parent.append(code);
    } else if (token.startsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = token.slice(2, -2);
      parent.append(strong);
    } else if (token.startsWith("*")) {
      const emphasis = document.createElement("em");
      emphasis.textContent = token.slice(1, -1);
      parent.append(emphasis);
    } else {
      const parts = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      const url = parts ? parts[2].trim() : "";
      if (/^(https?:|mailto:)/i.test(url)) {
        const link = document.createElement("a");
        link.textContent = parts[1];
        link.href = url;
        link.rel = "noopener noreferrer";
        link.target = "_blank";
        parent.append(link);
      } else parent.append(document.createTextNode(token));
    }
    cursor = match.index + token.length;
  }
  parent.append(document.createTextNode(source.slice(cursor)));
}

export function renderMarkdownSubset(text) {
  const fragment = document.createDocumentFragment();
  const lines = String(text).split("\n");
  let code = null;
  let list = null;
  for (const line of lines) {
    if (line.startsWith("```")) {
      if (code) { fragment.append(code); code = null; }
      else { code = document.createElement("pre"); code.append(document.createElement("code")); }
      list = null;
      continue;
    }
    if (code) { code.firstChild.textContent += `${line}\n`; continue; }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    const item = line.match(/^[-*]\s+(.+)$/);
    if (item) {
      if (!list) { list = document.createElement("ul"); fragment.append(list); }
      const li = document.createElement("li");
      appendInline(li, item[1]);
      list.append(li);
      continue;
    }
    list = null;
    const element = heading ? document.createElement(`h${heading[1].length}`) : document.createElement(line.startsWith("> ") ? "blockquote" : "p");
    appendInline(element, heading ? heading[2] : line.startsWith("> ") ? line.slice(2) : line);
    fragment.append(element);
  }
  if (code) fragment.append(code);
  return fragment;
}

// TODO: Add math and Mermaid only through audited, build-free renderers.
