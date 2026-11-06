// DOZYNKI similar objects: a PCA map of the feature vectors and each object's nearest neighbors. Plain JS, no build step.

const STORE_KEY = "dozynki.similar.v1";
const SYMBOLS = { bogus: "x", sso: "triangle-up", star: "star", agn: "diamond", transient: "circle", other: "square", unsure: "circle-open" };
// Color by TNS type: what the type maps to (tns.py), in the class colors; transient subclasses differ by symbol.
const TNS_GROUPS = [
  { key: "sn_like", name: "TNS SN-like", color: "transient", symbol: "circle", is: (t) => t.subclass === "sn_like" },
  { key: "nuclear", name: "TNS TDE", color: "transient", symbol: "hexagon", is: (t) => t.subclass === "nuclear" },
  { key: "cv_nova", name: "TNS CV / nova / flare", color: "transient", symbol: "triangle-down", is: (t) => t.subclass === "cv_nova" },
  { key: "fast", name: "TNS fast / other transient", color: "transient", symbol: "cross", is: (t) => t.klass === "transient" },
  { key: "agn", name: "TNS AGN / QSO", color: "agn", symbol: "diamond", is: (t) => t.klass === "agn" },
  { key: "star", name: "TNS variable star", color: "star", symbol: "star", is: (t) => t.klass === "star" },
  { key: "other", name: "TNS other type", color: "other", symbol: "square", is: (t) => !!t.type },
  { key: "unclassified", name: "TNS unclassified", color: "unsure", symbol: "circle", is: () => true },
];
const tnsGroup = (t) => TNS_GROUPS.find((g) => g.is(t));
const tnsUrl = (name) => `https://www.wis-tns.org/object/${encodeURIComponent(String(name).replace(/^\S+\s+/, ""))}`;

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
  return { truth: "blind", all_sources: false, object_list: "", vetted: "", survey: "lsst", unlabeled: true, hidden: [], groups: null, off: [], pool: "labeled", k: 8, selected: null,
           colorby: "label" };
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

const query = () => new URLSearchParams({ truth: state.truth, ...Population.params(state),
  groups: (state.groups || []).join(","), off: state.off.join(",") }).toString();

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
  const nt = DATA.objects.filter((o) => o.tns).length, nc = DATA.objects.filter((o) => o.tns?.type).length;
  $("#pop").innerHTML = `<b>${n}</b> objects with cached data ${esc(Population.phrase(state))}, <b>${nl}</b> labeled`
    + `, <b>${nt}</b> matched to TNS (${nc} classified) · <b>${DATA.columns.length}</b> features`;
  $("#unlabeled").nextSibling.textContent = ` unlabeled (${n - nl})`;

  // feature groups, with how many of their features have any spread here
  const used = {};
  for (const c of DATA.columns) used[c.group] = (used[c.group] || 0) + 1;
  $("#groups").innerHTML = Object.entries(DATA.groups).map(([g, label]) =>
    `<button data-g="${g}" class="${state.groups.includes(g) ? "on" : ""}">${esc(label)}${state.groups.includes(g) ? ` <span class="muted">${used[g] || 0}</span>` : ""}</button>`).join("")
    + `<details class="info"><summary title="More info">i</summary><div class="info-body">Broker scores are off by default: they are classifier output, not evidence.</div></details>`;
  $("#groups").querySelectorAll("button").forEach((b) => b.onclick = () => {
    const g = b.dataset.g;
    const next = state.groups.includes(g) ? state.groups.filter((x) => x !== g) : [...state.groups, g];
    if (!next.length) { toast("Keep at least one feature group"); return; }
    state.groups = next;
    load();
  });
  renderFeatures();
  $("#ids").innerHTML = DATA.objects.map((o) => `<option value="${esc(o.object_id)}">`).join("");
}

// Single features of the groups that are on; one that is on but has too few objects here (or no spread) is
// left out by similar.scale, and shown struck through.
function renderFeatures() {
  const kept = new Set(DATA.columns.map((c) => c.name));
  const groups = Object.keys(DATA.groups).filter((g) => state.groups.includes(g));
  $("#features").innerHTML = groups.map((g) => `<div class="fgroup"><span class="muted">${esc(DATA.groups[g])}</span> ${
    DATA.features.filter((f) => f.group === g).map((f) => {
      const off = state.off.includes(f.name), unused = !off && !kept.has(f.name);
      const tip = off ? "off: click to use it" : unused ? "on, but too few objects here have it (or it never varies), so it is left out" : "click to leave it out";
      return `<button data-f="${esc(f.name)}" class="${off ? "" : "on"}${unused ? " unused" : ""}" title="${esc(f.name)} · ${tip}">${esc(f.label)}</button>`;
    }).join("")}</div>`).join("");
  const nOff = DATA.features.filter((f) => state.groups.includes(f.group) && state.off.includes(f.name)).length;
  $("#pick-count").textContent = nOff ? `(${nOff} off)` : "";
  $("#features").querySelectorAll("button").forEach((b) => b.onclick = () => {
    const f = b.dataset.f;
    if (!state.off.includes(f) && kept.has(f) && kept.size <= 2) { toast("Keep at least two features"); return; }
    state.off = state.off.includes(f) ? state.off.filter((x) => x !== f) : [...state.off, f];
    load();
  });
}

// How much of each axis an object's own features carry (similar.pca's support); positions an object's
// features barely touch are guesses, drawn faint.
const support = (o) => Math.min(...(o.support || [1]));
const fade = (objs, base) => objs.map((o) => base * (0.12 + 0.88 * Math.min(1, support(o) / 0.5)));
const supportNote = (o) => {
  if (!o.support || support(o) >= 0.5) return "";
  const pct = (v) => `${Math.round(100 * v)}%`;
  return `<br><i>position partly a guess: its features carry ${pct(o.support[0])} of PC1, ${pct(o.support[1])} of PC2</i>`;
};

function renderMap() {
  const hover = (o) => `<b>${esc(o.object_id)}</b><br>${esc(o.klass ?? "unlabeled")}${o.subclass ? ` · ${esc(o.subclass)}` : ""}`
    + `${o.alt_classes.length ? ` (could be ${esc(o.alt_classes.join(", "))})` : ""}`
    + `${o.tns ? `<br>TNS ${esc(o.tns.name)} · ${esc(o.tns.type ?? "unclassified")}` : ""}<br>${o.n_features} of ${DATA.columns.length} features${supportNote(o)}`;
  const points = (objs) => ({ x: objs.map((o) => o.x), y: objs.map((o) => o.y), customdata: objs.map((o) => o.object_id),
                              text: objs.map(hover), hovertemplate: "%{text}<extra></extra>" });
  const trace = (objs, k, name) => ({
    type: "scatter", mode: "markers", name,
    x: objs.map((o) => o.x), y: objs.map((o) => o.y), customdata: objs.map((o) => o.object_id),
    text: objs.map(hover), hovertemplate: "%{text}<extra></extra>",
    marker: {
      color: color(k), symbol: k ? SYMBOLS[k] : "circle", size: k ? 8 : 5, opacity: fade(objs, k ? 0.85 : 0.6),
      line: { color: k === "unsure" ? color(k) : cssVar("--card"), width: k === "unsure" ? 1.5 : 0.5 },
    },
  });
  const traces = [];
  // Show picks objects by label in either coloring
  const shown = DATA.objects.filter((o) => (o.klass ? !state.hidden.includes(o.klass) : state.unlabeled));
  if (state.colorby === "tns") {
    const none = shown.filter((o) => !o.tns);
    if (none.length) traces.push({ type: "scatter", mode: "markers", name: "no TNS match", ...points(none),
                                   marker: { color: color(null), size: 4, opacity: fade(none, 0.5) } });
    for (const g of TNS_GROUPS) {
      const objs = shown.filter((o) => o.tns && tnsGroup(o.tns) === g);
      if (objs.length) traces.push({ type: "scatter", mode: "markers", name: `${g.name} (${objs.length})`, ...points(objs),
        marker: { color: color(g.color), symbol: g.symbol, size: 10, opacity: fade(objs, 0.9), line: { color: cssVar("--card"), width: 0.5 } } });
    }
  } else {
    if (state.unlabeled) traces.push(trace(DATA.objects.filter((o) => !o.klass), null, "unlabeled"));
    for (const c of DATA.classes) {
      if (state.hidden.includes(c.key)) continue;
      const objs = DATA.objects.filter((o) => o.klass === c.key);
      if (objs.length) traces.push(trace(objs, c.key, c.key));
    }
    const ringed = shown.filter((o) => o.tns);
    if (ringed.length) traces.push({ type: "scatter", mode: "markers", name: `TNS match (${ringed.length})`, ...points(ringed),
                                     marker: { size: 14, color: "rgba(0,0,0,0)", line: { color: cssVar("--ink"), width: 1 } } });
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
    ${o.tns ? `<div class="tns">TNS <a href="${tnsUrl(o.tns.name)}" target="_blank" rel="noopener">${esc(o.tns.name)}</a> · ${esc(o.tns.type ?? "unclassified")}</div>` : ""}
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

// Clicks can outrun the server (toggling feature groups quickly, say), and answers can come back out of
// order; each request takes a number, and an answer is only used if no newer request has gone out since.
let loadSeq = 0, selectSeq = 0;

async function select(id) {
  if (!id) return;
  const seq = ++selectSeq;
  try {
    const nn = await api(`/api/similar/${encodeURIComponent(id)}?k=${state.k}&pool=${state.pool}&${query()}`);
    if (seq !== selectSeq) return;
    NN = nn;
    state.selected = id;
  } catch (e) {
    if (seq !== selectSeq) return;
    NN = null;
    toast(`Could not find neighbors of ${id}: ${e.message}`, 6000);
  }
  render();
}

async function load() {
  const seq = ++loadSeq;
  selectSeq++;  // neighbors asked for under the old settings no longer match the map
  try {
    const data = await api(`/api/similar?${query()}`);
    if (seq !== loadSeq) return;
    DATA = data;
    state.groups = state.groups || DATA.default_groups;
    const still = DATA.objects.some((o) => o.object_id === state.selected);
    if (still) await select(state.selected);
    else { NN = null; state.selected = null; render(); }
  } catch (e) {
    if (seq === loadSeq) toast(`Could not load objects: ${e.message}`, 6000);
  }
}

async function init() {
  state = loadState();
  state.off = state.off || [];
  const fromUrl = new URLSearchParams(location.search).get("object");
  if (fromUrl) state.selected = fromUrl;
  bindSeg("#truth", () => state.truth, (v) => { state.truth = v; load(); });
  bindSeg("#colorby", () => state.colorby, (v) => { state.colorby = v; render(); });
  bindSeg("#pool", () => state.pool, (v) => { state.pool = v; select(state.selected); });
  $("#k").value = String(state.k);
  $("#k").onchange = (e) => { state.k = +e.target.value; select(state.selected); };
  Population.mount($("#population"), state, (p) => { Object.assign(state, p); load(); });
  $("#unlabeled").checked = state.unlabeled;
  $("#unlabeled").onchange = (e) => { state.unlabeled = e.target.checked; render(); };
  const find = () => select($("#find").value.trim());
  $("#find-go").onclick = find;
  $("#find").onkeydown = (e) => { if (e.key === "Enter") find(); };
  // the tab bar's search box: the object found becomes the selected one, with its neighbors
  window.onFind = async (hit) => {
    if (!DATA?.objects.some((o) => o.object_id === hit.object_id)) return `isn't on this map: it isn't among the objects ${Population.phrase(state)}`;
    $("#find").value = hit.object_id;
    await select(hit.object_id);
    $("#map").scrollIntoView({ block: "center", behavior: "smooth" });
    return null;
  };
  await load();
  $("#map").on("plotly_click", (ev) => select(ev.points[0]?.customdata));
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
}

init();
