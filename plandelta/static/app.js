"use strict";

// Document text is untrusted: it reaches the DOM only through textContent.
// The one exception is chart markup, which the server generates itself.

const token = new URLSearchParams(location.search).get("token") || "";
const el = (id) => document.getElementById(id);
const state = { pairs: [], current: null, snapshot: null, selected: null };

const STATUS_LABEL = {
  exceeded: "Exceeded", done: "Done", partial: "Partial", missed: "Missed",
  unknown: "Unknown", error: "Error", extra: "Unplanned",
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "X-Plandelta-Token": token, "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload?.error?.message || `HTTP ${response.status}`);
  return payload;
}

function setStatus(text) {
  el("status-line").textContent = text;
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

// Titles come from Markdown, so emphasis markers survive extraction. Strip them
// for display only — the stored title stays verbatim for keys and quoting.
// Underscores are left alone: identifiers like asset_store are the point of the
// title, and mangling them is worse than showing an italic marker.
function plainTitle(text) {
  return String(text).replace(/\*+|`+/g, "").replace(/\s+/g, " ").trim();
}

function badge(status) {
  return node("span", `badge bg-${status}`, STATUS_LABEL[status] || status);
}

function quoteBlock(evidence) {
  const block = node("blockquote");
  const cite = node("cite", null, `${evidence.file || "evidence"}:${evidence.line_start}-${evidence.line_end}`);
  block.append(cite, document.createTextNode(evidence.quote));
  return block;
}

/* ---------- sidebar ---------- */

function renderPairs() {
  const list = el("pair-list");
  list.textContent = "";
  const select = el("pair-select");
  select.textContent = "";

  state.pairs.forEach((pair) => {
    const li = node("li");
    const button = node("button", "pair-item");
    button.type = "button";
    if (pair.id === state.current) button.classList.add("is-active");
    button.append(
      node("span", `dot ${pair.dirty ? "" : "is-clean"}`),
      node("span", "pair-name", pair.title || pair.id),
      node("span", "muted", pair.totals ? `${pair.totals.rate.toFixed(0)}%` : "—"),
    );
    button.addEventListener("click", () => selectPair(pair.id));
    li.append(button);
    list.append(li);

    const option = node("option", null, pair.id);
    option.value = pair.id;
    if (pair.id === state.current) option.selected = true;
    select.append(option);
  });
}

/* ---------- main panes ---------- */

function renderKpis(totals) {
  const cells = [
    ["Completion rate", `${totals.rate.toFixed(1)}%`],
    ["Evidence coverage", `${totals.coverage.toFixed(1)}%`],
    ["Points", `${totals.points} / ${totals.max_points}`],
    ["Bonus / Penalty", `${totals.bonus >= 0 ? "+" : ""}${totals.bonus} / ${totals.penalty}`],
    ["Unplanned work", String(totals.scope_creep)],
  ];
  const container = el("kpis");
  container.textContent = "";
  cells.forEach(([label, value]) => {
    const card = node("div", "kpi");
    card.append(node("div", "label", label), node("div", "value", value));
    container.append(card);
  });
}

function renderItems(items) {
  const pane = el("plan-items");
  pane.textContent = "";
  if (!items.length) {
    pane.append(node("p", "empty", "No items in this snapshot."));
    return;
  }
  items.forEach((item, index) => {
    const article = node("article", "item");
    const head = node("div", "item-head");
    head.append(
      badge(item.status),
      node("span", "item-title", plainTitle(item.title)),
      node("span", "points", `${item.points >= 0 ? "+" : ""}${item.points}`),
    );
    if (item.lineage && item.lineage !== "same") head.append(node("span", "muted", item.lineage));
    article.append(head, node("p", "muted", item.section));
    article.addEventListener("click", () => selectItem(index));
    pane.append(article);
  });
  selectItem(0);
}

function selectItem(index) {
  state.selected = index;
  const items = state.snapshot?.items || [];
  [...el("plan-items").children].forEach((child, position) =>
    child.classList.toggle("is-selected", position === index),
  );
  const pane = el("evidence-body");
  pane.textContent = "";
  const item = items[index];
  if (!item) return;

  const head = node("div", "item-head");
  head.append(badge(item.status), node("span", "item-title", plainTitle(item.title)));
  pane.append(head);
  if (item.reason) pane.append(node("p", "reason", item.reason));
  if (!item.evidence.length) {
    pane.append(node("p", "empty", "No evidence was accepted for this item."));
  }
  item.evidence.forEach((evidence) => pane.append(quoteBlock(evidence)));

  const extras = state.snapshot?.extras || [];
  if (index === 0 && extras.length) {
    pane.append(node("h2", "section-title", `Unplanned work (${extras.length})`));
    extras.forEach((extra) => {
      pane.append(node("p", "reason", extra.reason), quoteBlock(extra));
    });
  }
}

// Charts arrive as SVG markup the server generated. The only untrusted text
// inside them is item titles, which the server escapes — but this is the one
// place markup crosses into the DOM, so it is parsed inert and stripped of
// anything executable before being adopted, rather than trusted on principle.
function setChart(target, markup) {
  const parsed = new DOMParser().parseFromString(markup, "text/html");
  parsed.body.querySelectorAll("script, foreignObject, iframe, object, embed").forEach((n) => n.remove());
  parsed.body.querySelectorAll("*").forEach((element) => {
    [...element.attributes].forEach((attribute) => {
      const name = attribute.name.toLowerCase();
      if (name.startsWith("on") || (name === "href" && attribute.value.trim().toLowerCase().startsWith("javascript:"))) {
        element.removeAttribute(attribute.name);
      }
    });
  });
  target.textContent = "";
  target.append(...parsed.body.childNodes);
}

function renderCharts(charts) {
  setChart(el("chart-donut"), charts.donut);
  setChart(el("chart-trend"), charts.trend);
  setChart(el("chart-stack"), charts.stack);
}

function renderRounds(rounds) {
  const select = el("round-select");
  select.textContent = "";
  rounds.forEach((round) => {
    const option = node("option", null, `#${round.round} · ${round.rate.toFixed(0)}% · ${round.created_at}`);
    option.value = String(round.snapshot_id);
    select.append(option);
  });
  if (rounds.length) select.lastChild.selected = true;
}

/* ---------- flow ---------- */

async function selectPair(pairId) {
  state.current = pairId;
  // Keep the address bar pointing at what is on screen, so a view can be
  // bookmarked, shared with a colleague, or screenshotted reproducibly.
  const address = new URL(location.href);
  address.searchParams.set("pair", pairId);
  history.replaceState(null, "", address);
  renderPairs();
  setStatus("loading…");
  const pair = state.pairs.find((candidate) => candidate.id === pairId);
  el("plan-name").textContent = pair ? pair.plan : "";
  el("done-name").textContent = pair ? pair.done.join(", ") : "";

  const [snapshot, rounds] = await Promise.all([
    api(`/api/pairs/${encodeURIComponent(pairId)}/latest`),
    api(`/api/pairs/${encodeURIComponent(pairId)}/snapshots`),
  ]);
  renderRounds(rounds);

  if (!snapshot.snapshot_id) {
    state.snapshot = null;
    el("kpis").textContent = "";
    el("plan-items").textContent = "";
    el("evidence-body").textContent = "";
    setStatus("never compared — press Recompare");
    return;
  }
  state.snapshot = snapshot;
  renderKpis(snapshot.totals);
  renderCharts(snapshot.charts);
  renderItems(snapshot.items);
  setStatus(`${snapshot.model} · ${snapshot.created_at}${pair && pair.dirty ? " · documents changed" : ""}`);
}

async function recompare() {
  if (!state.current) return;
  const button = el("recompare");
  button.disabled = true;
  setStatus("comparing… this calls the engine and can take a minute");
  try {
    await api(`/api/pairs/${encodeURIComponent(state.current)}/recompare`, { method: "POST", body: "{}" });
    await load(state.current);
  } catch (error) {
    setStatus(`failed: ${error.message}`);
  } finally {
    button.disabled = false;
  }
}

async function load(keepPairId) {
  state.pairs = await api("/api/pairs");
  const requested = new URLSearchParams(location.search).get("pair");
  const known = (id) => state.pairs.some((pair) => pair.id === id);
  const target =
    keepPairId || (known(requested) ? requested : null) || (state.pairs[0] && state.pairs[0].id);
  renderPairs();
  if (target) await selectPair(target);
  else setStatus("no pairs discovered under this root");
}

el("recompare").addEventListener("click", recompare);
el("pair-select").addEventListener("change", (event) => selectPair(event.target.value));

load().catch((error) => setStatus(`failed: ${error.message}`));
