// DOZYNKI sky map: labeled objects on the sky, and what Galactic / ecliptic latitude cuts would reject. Plain JS, no build step.

const STORE_KEY = "dozynki.sky.v1";
const LAB_KEY = "dozynki.filterlab.v1";  // the Filter lab's saved state, where pipeline A lives
const SYMBOLS = { bogus: "x", sso: "triangle-up", star: "star", agn: "diamond", transient: "circle", other: "square", unsure: "circle-open" };
const BIN = 5;  // histogram bin width, degrees
const DEG = Math.PI / 180;

// Rotation matrices from ICRS unit vectors to each frame (Galactic: Hipparcos/astropy; ecliptic: J2000 obliquity).
const EPS = 23.4392911 * DEG;
const FRAMES = {
  eq: { name: "Equatorial", lon: "RA", lat: "Dec", m: [[1, 0, 0], [0, 1, 0], [0, 0, 1]] },
  gal: { name: "Galactic", lon: "l", lat: "b", m: [
    [-0.0548755604162154, -0.8734370902348850, -0.4838350155487132],
    [0.4941094278755837, -0.4448296299600112, 0.7469822444972189],
    [-0.8676661490190047, -0.1980763734312015, 0.4559837761750669]] },
  ecl: { name: "Ecliptic", lon: "λ", lat: "β", m: [[1, 0, 0], [0, Math.cos(EPS), Math.sin(EPS)], [0, -Math.sin(EPS), Math.cos(EPS)]] },
};
const COORDS = { eq: ["ra", "dec"], gal: ["l", "b"], ecl: ["elon", "elat"] };

let DATA = null;
let PIPE = null;  // pipeline A: {steps, rejectedBy: {object_id: step key or null}}, or null if not evaluated
let state = null;

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const color = (k) => cssVar(`--cls-${k ?? "none"}`);

function toast(msg, ms = 3000) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), ms);
}

async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
  return r.json();
}

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
  return r.json();
}

function wilson(k, n, z = 1.96) {
  if (!n) return [null, null, null];
  const p = k / n, d = 1 + z * z / n;
  const centre = (p + z * z / (2 * n)) / d;
  const half = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d;
  return [p, Math.max(0, centre - half), Math.min(1, centre + half)];
}

// ---------- state ----------

function defaultState() {
  return { truth: "blind", all_sources: false, unlabeled: false, frame: "eq", hidden: [], cut_b: 0, cut_e: 0, pipe: "off", hist: { b: "count", e: "count" } };
}

function loadState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY));
    if (saved) return { ...defaultState(), ...saved, hist: { ...defaultState().hist, ...saved.hist } };
  } catch (e) { /* storage blocked or corrupt: start fresh */ }
  return defaultState();
}

function saveState() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch (e) { /* not essential */ }
}

// ---------- geometry ----------

const toVec = (lon, lat) => [Math.cos(lat * DEG) * Math.cos(lon * DEG), Math.cos(lat * DEG) * Math.sin(lon * DEG), Math.sin(lat * DEG)];
const fromVec = ([x, y, z]) => [Math.atan2(y, x) / DEG, Math.asin(Math.max(-1, Math.min(1, z))) / DEG];
const mul = (m, v) => m.map((r) => r[0] * v[0] + r[1] * v[1] + r[2] * v[2]);
const mulT = (m, v) => [0, 1, 2].map((j) => m[0][j] * v[0] + m[1][j] * v[1] + m[2][j] * v[2]);

// Hammer–Aitoff, centered on longitude 0, longitude increasing to the left (east left, as on the sky).
function project(lon, lat) {
  const l = (((lon + 180) % 360) + 360) % 360 - 180;
  const phi = lat * DEG, lam = l * DEG / 2;
  const d = Math.sqrt(1 + Math.cos(phi) * Math.cos(lam));
  return [-2 * Math.SQRT2 * Math.cos(phi) * Math.sin(lam) / d, Math.SQRT2 * Math.sin(phi) / d];
}

// A line of constant latitude in frame `from`, drawn in frame `to`; broken where it wraps the map edge.
function latitudeLine(from, lat, to) {
  const xs = [], ys = [];
  let prev = null;
  for (let lon = 0; lon <= 360; lon += 1) {
    const v = mul(FRAMES[to].m, mulT(FRAMES[from].m, toVec(lon, lat)));
    const [x, y] = project(...fromVec(v));
    if (prev !== null && Math.abs(x - prev) > 1) { xs.push(null); ys.push(null); }
    xs.push(x); ys.push(y);
    prev = x;
  }
  return { xs, ys };
}

function graticule() {
  const xs = [], ys = [];
  for (let lon = -180; lon <= 180; lon += 60) {
    for (let lat = -90; lat <= 90; lat += 2) { const [x, y] = project(lon === 180 ? 179.999 : lon, lat); xs.push(x); ys.push(y); }
    xs.push(null); ys.push(null);
  }
  for (let lat = -60; lat <= 60; lat += 30) {
    for (let lon = -179.999; lon <= 180; lon += 2) { const [x, y] = project(lon, lat); xs.push(x); ys.push(y); }
    xs.push(null); ys.push(null);
  }
  return { xs, ys };
}

// ---------- cuts ----------

const rejectedB = (o) => state.cut_b > 0 && Math.abs(o.b) < state.cut_b;
const rejectedE = (o) => state.cut_e > 0 && Math.abs(o.elat) < state.cut_e;
const rejected = (o) => rejectedB(o) || rejectedE(o);
const rejectedA = (o) => !!PIPE?.rejectedBy[o.object_id];
const faded = (o) => rejected(o) || (state.pipe === "fade" && rejectedA(o));
const stepColor = (s) => cssVar(`--step-${(PIPE.steps.indexOf(s) % 8) + 1}`);

// ---------- rendering ----------

function plotLayout(extra = {}) {
  const ink = cssVar("--muted"), line = cssVar("--line");
  return {
    margin: { l: 50, r: 10, t: 10, b: 40 },
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: "system-ui, sans-serif", size: 12, color: ink },
    legend: { orientation: "h", y: -0.18 },
    hoverlabel: { bgcolor: cssVar("--card"), bordercolor: line, font: { color: cssVar("--ink") } },
    ...extra,
  };
}

const labeledClasses = () => DATA.classes.map((c) => c.key).filter((k) => DATA.objects.some((o) => o.klass === k));

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
  $("#pop").innerHTML = `<b>${nl}</b> labeled · <b>${n - nl}</b> unlabeled ${state.all_sources ? "queued objects" : "in the random sample"}`;
  $("#unlabeled").nextSibling.textContent = ` unlabeled (${n - nl})`;
}

function renderMap() {
  const f = state.frame, [lonKey, latKey] = COORDS[f], line = cssVar("--line"), muted = cssVar("--muted");
  const g = graticule();
  const traces = [{ type: "scatter", mode: "lines", x: g.xs, y: g.ys, line: { color: line, width: 1 }, hoverinfo: "skip", showlegend: false }];
  const ref = (from, lat, name, dash, width) => {
    const c = latitudeLine(from, lat, f);
    return { type: "scatter", mode: "lines", x: c.xs, y: c.ys, name, line: { color: muted, width, dash }, hoverinfo: "skip", showlegend: false };
  };
  traces.push(ref("gal", 0, "Galactic plane", "solid", 1.5), ref("ecl", 0, "Ecliptic", "dot", 1.5));
  for (const s of [-1, 1]) {
    if (state.cut_b > 0) traces.push(ref("gal", s * state.cut_b, `|b| = ${state.cut_b}°`, "dash", 1));
    if (state.cut_e > 0) traces.push(ref("ecl", s * state.cut_e, `|β| = ${state.cut_e}°`, "dashdot", 1));
  }

  const hover = (o) => `<b>${esc(o.object_id)}</b><br>${esc(o.klass ?? "unlabeled")}${o.subclass ? ` · ${esc(o.subclass)}` : ""}`
    + `${o.alt_classes.length ? ` (could be ${esc(o.alt_classes.join(", "))})` : ""}`
    + `<br>RA ${o.ra.toFixed(3)} Dec ${o.dec.toFixed(3)}<br>l ${o.l.toFixed(1)} b ${o.b.toFixed(1)} · β ${o.elat.toFixed(1)}`
    + (rejected(o) ? `<br><i>rejected by ${[rejectedB(o) && "|b|", rejectedE(o) && "|β|"].filter(Boolean).join(" and ")} cut</i>` : "")
    + (state.pipe !== "off" && PIPE ? `<br>pipeline A: ${o.object_id in PIPE.rejectedBy ? (rejectedA(o) ? `rejected by <b>${esc(PIPE.rejectedBy[o.object_id])}</b>` : "kept") : "not scored"}` : "");
  const pointTrace = (objs, k, name = k ?? "unlabeled", c = color(k)) => {
    const xy = objs.map((o) => project(o[lonKey], o[latKey]));
    return {
      type: "scatter", mode: "markers", name,
      x: xy.map((p) => p[0]), y: xy.map((p) => p[1]), customdata: objs.map((o) => o.object_id),
      text: objs.map(hover), hovertemplate: "%{text}<extra></extra>",
      marker: {
        color: c, symbol: k === undefined ? objs.map((o) => SYMBOLS[o.klass]) : k ? SYMBOLS[k] : "circle", size: k === null ? 5 : 9,
        opacity: objs.map((o) => (faded(o) ? 0.2 : k === null ? 0.7 : 0.9)),
        line: { color: k === "unsure" ? c : cssVar("--card"), width: k === "unsure" ? 1.5 : 0.5 },
      },
    };
  };
  if (state.unlabeled) traces.push(pointTrace(DATA.objects.filter((o) => !o.klass), null, "unlabeled", color(null)));
  const shown = DATA.objects.filter((o) => o.klass && !state.hidden.includes(o.klass));
  if (state.pipe === "step" && PIPE) {
    // one trace per rejecting step (class still shown by symbol); kept and unscored objects in gray underneath
    traces.push(pointTrace(shown.filter((o) => !(o.object_id in PIPE.rejectedBy)), undefined, "not scored", color("unsure")));
    traces.push(pointTrace(shown.filter((o) => o.object_id in PIPE.rejectedBy && !rejectedA(o)), undefined, "kept by A", color(null)));
    for (const s of PIPE.steps) {
      const objs = shown.filter((o) => PIPE.rejectedBy[o.object_id] === s);
      if (objs.length) traces.push(pointTrace(objs, undefined, `${s} (${objs.length})`, stepColor(s)));
    }
  } else {
    for (const k of labeledClasses()) {
      if (!state.hidden.includes(k)) traces.push(pointTrace(shown.filter((o) => o.klass === k), k));
    }
  }

  // Legend swatches drawn separately: Plotly copies the first point's opacity (faded when rejected) and, in step
  // mode, its symbol (which there means class, not step).
  for (const t of traces.filter((t) => t.mode === "markers")) {
    t.showlegend = false;
    t.legendgroup = t.name;
    traces.push({ type: "scatter", mode: "markers", x: [null], y: [null], name: t.name, legendgroup: t.name, hoverinfo: "skip",
                  marker: { ...t.marker, symbol: Array.isArray(t.marker.symbol) ? "circle" : t.marker.symbol, opacity: 1 } });
  }

  // longitude labels along the equator (east left)
  const ann = [-120, -60, 0, 60, 120].map((lon) => {
    const [x, y] = project(lon, 0);
    const shown = f === "eq" ? `${((lon + 360) % 360) / 15}h` : `${(lon + 360) % 360}°`;
    return { x, y, text: shown, showarrow: false, font: { size: 10, color: muted }, yshift: -8 };
  });
  const ax = { visible: false, fixedrange: false };
  Plotly.react("map", traces, plotLayout({
    margin: { l: 10, r: 10, t: 10, b: 10 }, showlegend: true, legend: { orientation: "h", y: -0.02 },
    xaxis: { ...ax, range: [-2.9, 2.9] }, yaxis: { ...ax, range: [-1.45, 1.45], scaleanchor: "x" },
    annotations: ann, dragmode: "zoom",
  }), { displaylogo: false, responsive: true });
}

function pctCell(k, n) {
  const [p, lo, hi] = wilson(k, n);
  return p === null ? "—" : `${(100 * p).toFixed(1)}% <span class="ci">[${(100 * lo).toFixed(1)}, ${(100 * hi).toFixed(1)}]</span>`;
}

// Pipeline A columns for a set of objects, counting only the ones it scored
function pipeCells(objs) {
  if (!PIPE) return "";
  const scored = objs.filter((o) => o.object_id in PIPE.rejectedBy);
  const nA = scored.filter(rejectedA).length, nAny = scored.filter((o) => rejectedA(o) || rejected(o)).length;
  return `<td class="num">${nA}/${scored.length} · ${pctCell(nA, scored.length)}</td><td class="num">${nAny} · ${pctCell(nAny, scored.length)}</td>`;
}

function renderCutTable() {
  const rows = labeledClasses().map((k) => {
    const objs = DATA.objects.filter((o) => o.klass === k);
    const n = objs.length, nb = objs.filter(rejectedB).length, ne = objs.filter(rejectedE).length, nr = objs.filter(rejected).length;
    const [p, lo, hi] = wilson(nr, n);
    return `<tr><td><span class="sw" style="background:var(--cls-${k})"></span>${esc(k)}</td>
      <td class="num">${n}</td><td class="num">${state.cut_b > 0 ? nb : "—"}</td><td class="num">${state.cut_e > 0 ? ne : "—"}</td>
      <td class="num">${nr}</td><td class="num">${pctCell(nr, n)}</td>${pipeCells(objs)}</tr>`;
  });
  const all = DATA.objects.filter((o) => o.klass);
  $("#cut-table").innerHTML = `<tr><th>class</th><th class="num">labeled</th><th class="num">|b| cut</th><th class="num">|β| cut</th>
    <th class="num">either</th><th class="num">rejected</th>${PIPE ? `<th class="num" title="Rejected by pipeline A, of the objects it scored">pipeline A</th>
    <th class="num" title="Rejected by pipeline A or a latitude cut: what the latitude cuts would add to A">A or latitude</th>` : ""}</tr>${rows.join("")}
    <tr><td><b>all</b></td><td class="num">${all.length}</td><td class="num">${state.cut_b > 0 ? all.filter(rejectedB).length : "—"}</td>
    <td class="num">${state.cut_e > 0 ? all.filter(rejectedE).length : "—"}</td><td class="num">${all.filter(rejected).length}</td>
    <td class="num">${all.length ? (100 * all.filter(rejected).length / all.length).toFixed(1) : "—"}%</td>${pipeCells(all)}</tr>`;
  $("#cut-b-v").textContent = state.cut_b;
  $("#cut-e-v").textContent = state.cut_e;
}

function renderHist(which) {
  const key = which === "b" ? "b" : "elat", cut = which === "b" ? state.cut_b : state.cut_e;
  const edges = Array.from({ length: 90 / BIN }, (_, i) => i * BIN);
  const classes = labeledClasses().filter((k) => !state.hidden.includes(k));
  const counts = Object.fromEntries(classes.map((k) => [k, edges.map(() => 0)]));
  const totals = edges.map(() => 0);
  for (const o of DATA.objects) {
    if (!counts[o.klass]) continue;
    const i = Math.min(edges.length - 1, Math.floor(Math.abs(o[key]) / BIN));
    counts[o.klass][i]++;
    totals[i]++;
  }
  const frac = state.hist[which] === "frac";
  const x = edges.map((e) => e + BIN / 2);
  const traces = classes.map((k) => ({
    type: "bar", name: k, x, width: BIN * 0.92,
    y: counts[k].map((c, i) => (frac ? (totals[i] ? c / totals[i] : 0) : c)),
    customdata: counts[k].map((c, i) => [c, totals[i], edges[i], edges[i] + BIN]),
    hovertemplate: `${k}: %{customdata[0]} of %{customdata[1]} at |${which === "b" ? "b" : "β"}| %{customdata[2]}–%{customdata[3]}°<extra></extra>`,
    marker: { color: color(k), line: { color: cssVar("--card"), width: 1 } },
  }));
  const shapes = cut > 0 ? [{ type: "line", x0: cut, x1: cut, yref: "paper", y0: 0, y1: 1, line: { color: cssVar("--muted"), width: 1.5, dash: "dash" } }] : [];
  Plotly.react(`hist-${which}`, traces, plotLayout({
    barmode: "stack", shapes, margin: { l: 50, r: 10, t: 30, b: 40 },
    legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", traceorder: "normal" },
    xaxis: { title: which === "b" ? "|Galactic latitude b| (°)" : "|ecliptic latitude β| (°)", range: [0, 90], gridcolor: cssVar("--line"), dtick: 10 },
    yaxis: { title: frac ? "fraction of labeled" : "objects", gridcolor: cssVar("--line"), rangemode: "tozero", ...(frac ? { range: [0, 1] } : {}) },
  }), { displaylogo: false, responsive: true });
}

function render() {
  saveState();
  renderChips();
  renderMap();
  renderCutTable();
  renderHist("b");
  renderHist("e");
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

// Pipeline A's enabled steps as the Filter lab saved them, or the default pipeline if the lab was never opened here.
async function pipelineA() {
  try {
    const lab = JSON.parse(localStorage.getItem(LAB_KEY));
    if (lab && Array.isArray(lab.A)) return { pipeline: lab.A.filter((s) => s.on).map((s) => ({ rule: s.rule, ...s.params })), saved: true };
  } catch (e) { /* storage blocked or corrupt: use the default */ }
  return { pipeline: (await api("/api/rejection/config")).default_pipeline, saved: false };
}

async function loadPipe() {
  PIPE = null;
  if (state.pipe === "off") { $("#pipe-info").innerHTML = ""; return; }
  $("#pipe-info").textContent = "Evaluating pipeline A…";
  try {
    const { pipeline, saved } = await pipelineA();
    const r = await post("/api/rejection/evaluate", { pipeline, truth: state.truth, all_sources: state.all_sources, target: ["transient"] });
    PIPE = { steps: Object.keys(r.steps), rejectedBy: Object.fromEntries(r.objects.map((o) => [o.object_id, o.rejected_by])) };
    $("#pipe-info").innerHTML = `Pipeline A${saved ? "" : " (default; not yet edited in the Filter lab)"}: `
      + (PIPE.steps.map((s) => `<code>${esc(s)}</code>`).join(" → ") || "no rules enabled")
      + ` · rejects ${r.pipeline_metrics.rejected} of ${r.population.scored} scored. <a href="/rejection">Edit in the Filter lab</a>; this page follows your changes.`;
  } catch (e) {
    $("#pipe-info").textContent = `Could not evaluate pipeline A: ${e.message}`;
  }
}

async function load() {
  try {
    [DATA] = await Promise.all([api(`/api/sky?truth=${state.truth}&all_sources=${state.all_sources}`), loadPipe()]);
    render();
  } catch (e) {
    toast(`Could not load objects: ${e.message}`, 6000);
  }
}

async function init() {
  state = loadState();
  bindSeg("#truth", () => state.truth, (v) => { state.truth = v; load(); });
  bindSeg("#frame", () => state.frame, (v) => { state.frame = v; render(); });
  bindSeg("#pipe-mode", () => state.pipe, async (v) => { state.pipe = v; await loadPipe(); render(); });
  // the Filter lab in another tab saved a change to pipeline A
  window.addEventListener("storage", async (e) => { if (e.key === LAB_KEY && state.pipe !== "off") { await loadPipe(); render(); } });
  for (const w of ["b", "e"]) bindSeg(`[data-hist="${w}"]`, () => state.hist[w], (v) => { state.hist[w] = v; render(); });
  $("#all-sources").checked = state.all_sources;
  $("#all-sources").onchange = (e) => { state.all_sources = e.target.checked; load(); };
  $("#unlabeled").checked = state.unlabeled;
  $("#unlabeled").onchange = (e) => { state.unlabeled = e.target.checked; render(); };
  for (const [id, k] of [["#cut-b", "cut_b"], ["#cut-e", "cut_e"]]) {
    $(id).value = state[k];
    $(id).oninput = (e) => { state[k] = +e.target.value; render(); };
  }
  await load();
  $("#map").on("plotly_click", (ev) => {
    const id = ev.points[0]?.customdata;
    if (id) window.open(`/vet?object=${encodeURIComponent(id)}`, "_blank");
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
}

init();
