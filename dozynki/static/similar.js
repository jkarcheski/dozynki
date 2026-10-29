// DOZYNKI similar objects: a PCA map of the feature vectors and each object's nearest neighbors. Plain JS, no build step.

const STORE_KEY = "dozynki.similar.v1";
const SYMBOLS = { bogus: "x", sso: "triangle-up", star: "star", agn: "diamond", transient: "circle", other: "square", unsure: "circle-open" };

let DATA = null;  // /api/similar
let NN = null;    // /api/similar/{id} for the selected object
let state = null;

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const color = (k) => cssVar(`--cls-${k ?? "none"}`);
const vetLink = (id) => `/vet?object=${encodeURIComponent(id)}`;

function toast(msg, ms = 3000) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), ms);
}

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
  return r.json();
}

function fmt(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v !== "number") return esc(v);
  if (Number.isInteger(v)) return String(v);
  const a = Math.abs(v);
  return a >= 1000 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : v.toFixed(2);
}

// ---------- state ----------

function defaultState() {
  return { truth: "blind", all_sources: false, unlabeled: true, hidden: [], groups: null, pool: "labeled", k: 8, selected: null };
}

function loadState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY));
    if (saved) return { ...defaultState(), ...saved };
  } catch (e) { /* storage blocked or corrupt: start fresh */ }
  return defaultState();
}

function saveState() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch (e) { /* not essential */ }
}

const query = () => `truth=${state.truth}&all_sources=${state.all_sources}&groups=${(state.groups || []).join(",")}`;

// ---------- rendering ----------

function plotLayout(extra = {}) {
  const ink = cssVar("--muted"), line = cssVar("--line");
  return {
    margin: { l: 50, r: 10, t: 10, b: 40 },
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: "system-ui, sans-serif", size: 12, color: ink },
    legend: { orientation: "h", y: -0.12 },
    hoverlabel: { bgcolor: cssVar("--card"), bordercolor: line, font: { color: cssVar("--ink") } },
    ...extra,
  };
}

function renderChips() {
  const counts = {};
  for (const o of DATA.objects) counts[o.klass] = (counts[o.klass] || 0) + 1;
  $("#show-classes").innerHTML = DATA.classes.filter((c) => counts[c.key]).map((c) =>
    `<button data-k="${c.key}" class="${state.hidden.includes(c.key) ? "" : "on"}" style="--c:var(--cls-${c.key})" title="${esc(c.label)}">
      <i></i>${esc(c.key)} <span class="muted">${counts[c.key]}</span></button>`).join("");
  $("#show-classes").querySelectorAll("button").forEach((b) => b.onclick = () => {
    const k = b.dataset.k;
    state.hidden = state.hidden.includes(k) ? state.hidden.filter((x) => x !== k) : [...state.hidden, k];
    render();
  });
  const n = DATA.objects.length, nl = DATA.objects.filter((o) => o.klass).length;
  $("#pop").innerHTML = `<b>${n}</b> objects with cached data ${state.all_sources ? "in the queue" : "in the random sample"}, <b>${nl}</b> labeled`
    + ` · <b>${DATA.columns.length}</b> features`;
  $("#unlabeled").nextSibling.textContent = ` unlabeled (${n - nl})`;

  // feature groups, with how many of their features have any spread here
  const used = {};
  for (const c of DATA.columns) used[c.group] = (used[c.group] || 0) + 1;
  $("#groups").innerHTML = Object.entries(DATA.groups).map(([g, label]) =>
    `<button data-g="${g}" class="${state.groups.includes(g) ? "on" : ""}">${esc(label)}${state.groups.includes(g) ? ` <span class="muted">${used[g] || 0}</span>` : ""}</button>`).join("")
    + `<span class="hint">Broker scores are off by default: they are classifier output, not evidence.</span>`;
  $("#groups").querySelectorAll("button").forEach((b) => b.onclick = () => {
    const g = b.dataset.g;
    const next = state.groups.includes(g) ? state.groups.filter((x) => x !== g) : [...state.groups, g];
    if (!next.length) { toast("Keep at least one feature group"); return; }
    state.groups = next;
    load();
  });
  $("#ids").innerHTML = DATA.objects.map((o) => `<option value="${esc(o.object_id)}">`).join("");
}

function renderMap() {
  const hover = (o) => `<b>${esc(o.object_id)}</b><br>${esc(o.klass ?? "unlabeled")}${o.subclass ? ` · ${esc(o.subclass)}` : ""}`
    + `${o.alt_classes.length ? ` (could be ${esc(o.alt_classes.join(", "))})` : ""}<br>${o.n_features} of ${DATA.columns.length} features`;
  const trace = (objs, k, name) => ({
    type: "scatter", mode: "markers", name,
    x: objs.map((o) => o.x), y: objs.map((o) => o.y), customdata: objs.map((o) => o.object_id),
    text: objs.map(hover), hovertemplate: "%{text}<extra></extra>",
    marker: {
      color: color(k), symbol: k ? SYMBOLS[k] : "circle", size: k ? 8 : 5, opacity: k ? 0.85 : 0.6,
      line: { color: k === "unsure" ? color(k) : cssVar("--card"), width: k === "unsure" ? 1.5 : 0.5 },
    },
  });
  const traces = [];
  if (state.unlabeled) traces.push(trace(DATA.objects.filter((o) => !o.klass), null, "unlabeled"));
  for (const c of DATA.classes) {
    if (state.hidden.includes(c.key)) continue;
    const objs = DATA.objects.filter((o) => o.klass === c.key);
    if (objs.length) traces.push(trace(objs, c.key, c.key));
  }

  // the selected object, with lines to its neighbors
  const at = Object.fromEntries(DATA.objects.map((o) => [o.object_id, o]));
  const q = NN && at[NN.query.object_id];
  if (q) {
    const xs = [], ys = [];
    for (const n of NN.neighbors) {
      const p = at[n.object_id];
      if (p) xs.push(q.x, p.x, null), ys.push(q.y, p.y, null);
    }
    const ring = cssVar("--ink");
    traces.push({ type: "scatter", mode: "lines", x: xs, y: ys, line: { color: ring, width: 1 }, opacity: 0.5, hoverinfo: "skip", showlegend: false });
    const nb = NN.neighbors.map((n) => at[n.object_id]).filter(Boolean);
    traces.push({ type: "scatter", mode: "markers", name: "neighbors", x: nb.map((p) => p.x), y: nb.map((p) => p.y),
                  customdata: nb.map((p) => p.object_id), text: nb.map(hover), hovertemplate: "%{text}<extra></extra>",
                  marker: { size: 13, color: "rgba(0,0,0,0)", line: { color: ring, width: 1.5 } } });
    traces.push({ type: "scatter", mode: "markers", name: "selected", x: [q.x], y: [q.y], customdata: [q.object_id],
                  text: [hover(q)], hovertemplate: "%{text}<extra></extra>",
                  marker: { size: 17, color: "rgba(0,0,0,0)", line: { color: cssVar("--accent"), width: 3 } } });
  }

  const pc = (i) => `PC${i + 1} (${(100 * (DATA.explained[i] || 0)).toFixed(0)}% of variance)`;
  const grid = { gridcolor: cssVar("--line"), zerolinecolor: cssVar("--line") };
  Plotly.react("map", traces, plotLayout({
    margin: { l: 50, r: 10, t: 10, b: 50 }, legend: { orientation: "h", x: 0, y: 1, yanchor: "bottom" },
    xaxis: { title: pc(0), ...grid }, yaxis: { title: pc(1), ...grid }, dragmode: "zoom", uirevision: query(),
  }), { displaylogo: false, responsive: true });
}

function renderAxes() {
  $("#axes").innerHTML = DATA.loadings.map((ws, i) => {
    const top = ws.slice(0, 6), max = Math.max(...top.map((w) => Math.abs(w.weight)), 1e-9);
    return `<div><h4>PC${i + 1} is mostly</h4>${top.map((w) => {
      const f = Math.abs(w.weight) / max * 50;
      const bar = w.weight >= 0 ? `left:50%;width:${f}%` : `left:${50 - f}%;width:${f}%`;
      return `<div class="w"><span>${esc(w.label)}</span><span class="bar"><i style="${bar}"></i></span><span class="num">${w.weight >= 0 ? "+" : ""}${w.weight.toFixed(2)}</span></div>`;
    }).join("")}</div>`;
  }).join("");
}

function tile(o, isQuery) {
  const th = o.thumbs || {};
  const img = (k) => th[k] ? `<img src="${esc(th[k])}" alt="${k} cutout" loading="lazy">` : `<div class="noimg">no ${k}</div>`;
  const label = o.klass ? `${esc(o.klass)}${o.subclass ? ` · ${esc(o.subclass)}` : ""}` : "unlabeled";
  const names = Object.fromEntries(NN.columns.map((c) => [c.name, c.label]));
  return `<div class="tile${isQuery ? " query" : ""}" style="--c:var(--cls-${o.klass ?? "none"})">
    <div class="cuts2">${img("difference")}${img("science")}</div>
    <div class="cls">${label}${o.alt_classes.length ? ` <span class="muted">/ ${esc(o.alt_classes.join(", "))}</span>` : ""}</div>
    <a href="${vetLink(o.object_id)}" target="_blank">${esc(o.object_id)}</a>
    <div class="meta">${isQuery ? `selected · ${o.n_features} features` : `distance ${o.distance.toFixed(2)} · ${o.shared} shared`}</div>
    ${isQuery ? "" : `<div class="differs">differs on ${o.differs.map(([f, d]) => `${esc(names[f] || f)} (${d >= 0 ? "+" : ""}${d.toFixed(1)})`).join(", ") || "nothing"}</div>`}
  </div>`;
}

function renderNeighbors() {
  if (!NN) return;
  $("#query-name").textContent = `of ${NN.query.object_id} · ${NN.neighbors.length} of ${NN.pool_size} candidates`;
  $("#tiles").innerHTML = [tile(NN.query, true), ...NN.neighbors.map((n) => tile(n, false))].join("");
  if (!NN.neighbors.length) $("#tiles").insertAdjacentHTML("beforeend", `<span class="muted">No neighbors share enough features.</span>`);

  const objs = [NN.query, ...NN.neighbors];
  const head = `<tr><th>feature</th>${objs.map((o, i) => `<th class="num${i ? "" : " q"}" title="${esc(o.object_id)}">
    <span class="sw" style="background:var(--cls-${o.klass ?? "none"})"></span>${i ? `#${i}` : "selected"}</th>`).join("")}</tr>`;
  let group = null;
  const rows = NN.columns.map((c) => {
    const g = c.group !== group ? `<tr><td class="grp" colspan="${objs.length + 1}">${esc(DATA.groups[c.group])}</td></tr>` : "";
    group = c.group;
    return `${g}<tr><td title="${esc(c.name)} · present for ${(100 * c.coverage).toFixed(0)}% of objects">${esc(c.label)}</td>${
      objs.map((o, i) => `<td class="num${i ? "" : " q"}">${fmt(o.values[c.name])}</td>`).join("")}</tr>`;
  });
  $("#compare-table").innerHTML = head + rows.join("");
}

function render() {
  saveState();
  renderChips();
  renderMap();
  renderAxes();
  renderNeighbors();
}

// ---------- controls ----------

function bindSeg(sel, get, set) {
  const el = $(sel);
  el.querySelectorAll("button").forEach((b) => {
    b.classList.toggle("on", b.dataset.v === get());
    b.onclick = () => {
      el.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      set(b.dataset.v);
    };
  });
}

async function select(id) {
  if (!id) return;
  try {
    NN = await api(`/api/similar/${encodeURIComponent(id)}?k=${state.k}&pool=${state.pool}&${query()}`);
    state.selected = id;
  } catch (e) {
    NN = null;
    toast(`Could not find neighbors of ${id}: ${e.message}`, 6000);
  }
  render();
}

async function load() {
  try {
    DATA = await api(`/api/similar?${query()}`);
    state.groups = state.groups || DATA.default_groups;
    const still = DATA.objects.some((o) => o.object_id === state.selected);
    if (still) await select(state.selected);
    else { NN = null; state.selected = null; render(); }
  } catch (e) {
    toast(`Could not load objects: ${e.message}`, 6000);
  }
}

async function init() {
  state = loadState();
  const fromUrl = new URLSearchParams(location.search).get("object");
  if (fromUrl) state.selected = fromUrl;
  bindSeg("#truth", () => state.truth, (v) => { state.truth = v; load(); });
  bindSeg("#pool", () => state.pool, (v) => { state.pool = v; select(state.selected); });
  $("#k").value = String(state.k);
  $("#k").onchange = (e) => { state.k = +e.target.value; select(state.selected); };
  $("#all-sources").checked = state.all_sources;
  $("#all-sources").onchange = (e) => { state.all_sources = e.target.checked; load(); };
  $("#unlabeled").checked = state.unlabeled;
  $("#unlabeled").onchange = (e) => { state.unlabeled = e.target.checked; render(); };
  const find = () => select($("#find").value.trim());
  $("#find-go").onclick = find;
  $("#find").onkeydown = (e) => { if (e.key === "Enter") find(); };
  await load();
  $("#map").on("plotly_click", (ev) => select(ev.points[0]?.customdata));
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
}

init();
