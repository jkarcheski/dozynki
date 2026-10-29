// DOZYNKI filter lab: build a rejection pipeline and score it against the labels. Plain JS, no build step.

const STORE_KEY = "dozynki.filterlab.v1";
const METRICS = [
  { key: "completeness", label: "Completeness", hint: "target kept / target", higher: true },
  { key: "purity", label: "Purity", hint: "target kept / kept", higher: true },
  { key: "fpr", label: "False-positive rate", hint: "non-target kept / non-target", higher: false },
  { key: "bogus_leak", label: "Bogus leak", hint: "bogus kept / bogus", higher: false },
];
// default sweep range per parameter name
const SWEEP_RANGE = { max_ext: [0, 1], max_chi2: [1, 10], max_ratio: [0.5, 4], min_ratio: [0, 1], min_snr: [5, 30], max_len: [0, 10], max_dist: [0, 5], min_sg: [0, 1], min_beta: [0, 30], max_sep: [0, 5], n: [1, 10], max_frac: [0, 1], max_rms: [0, 2], min_rel: [0, 1], max_p: [0, 1], days: [0, 30], min_b: [0, 30] };
const INTEGER_PARAMS = new Set(["n"]);

let CFG = null;
let state = null;
let result = null;
let evalToken = 0;

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pct = (v) => (v === null || v === undefined ? "—" : v.toFixed(3));
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function toast(msg, ms = 3000) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), ms);
}

async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
  return r.json();
}

// ---------- state ----------

// A pipeline is every rule, in order, each {rule, on, params}; disabled rules keep their params.
function pipelineFrom(steps) {
  const byName = Object.fromEntries(CFG.rules.map((r) => [r.name, r]));
  const listed = steps.map((s) => s.rule);
  const order = [...listed, ...CFG.rules.map((r) => r.name).filter((n) => !listed.includes(n))];
  return order.map((name) => {
    const given = steps.find((s) => s.rule === name);
    const params = { ...byName[name].defaults };
    if (given) for (const k of Object.keys(params)) if (k in given) params[k] = given[k];
    return { rule: name, on: !!given, params };
  });
}

function defaultState() {
  return { target: ["transient"], include_alt: false, truth: "blind", all_sources: false, pipeline: pipelineFrom(CFG.default_pipeline), name: null };
}

function loadState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY));
    const steps = saved && (saved.pipeline || saved.A);  // A: from when the lab compared two pipelines
    if (Array.isArray(steps)) {
      // drop rules that no longer exist, append new ones (off), and fill in new params
      const byName = Object.fromEntries(CFG.rules.map((r) => [r.name, r]));
      const kept = steps.filter((s) => byName[s.rule])
        .map((s) => ({ rule: s.rule, on: !!s.on, params: { ...byName[s.rule].defaults, ...s.params } }));
      const missing = CFG.rules.filter((r) => !kept.some((s) => s.rule === r.name))
        .map((r) => ({ rule: r.name, on: false, params: { ...r.defaults } }));
      const { A, B, names, ...rest } = saved;
      return { ...defaultState(), ...rest, pipeline: [...kept, ...missing], name: saved.name ?? names?.A ?? null };
    }
  } catch (e) { /* storage blocked or corrupt: start fresh */ }
  return defaultState();
}

function saveState() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch (e) { /* not essential */ }
}

const requestFor = () => ({
  pipeline: state.pipeline.filter((s) => s.on).map((s) => ({ rule: s.rule, ...s.params })),
  truth: state.truth,
  target: state.target,
  include_alt: state.include_alt,
  all_sources: state.all_sources,
});

// ---------- settings ----------

const targetList = () => [
  ...CFG.classes.filter((c) => c.real !== null).map((c) => ({ key: c.key, label: c.label })),
  ...CFG.transient_subclasses.map((s) => ({ key: `transient:${s.key}`, label: `transient · ${s.label}` })),
];

function renderSettings() {
  $("#targets").innerHTML = targetList()
    .map((t) => `<button data-k="${t.key}" class="${state.target.includes(t.key) ? "on" : ""}" title="${esc(t.key)}">${esc(t.label)}</button>`)
    .join("");
  $("#targets").querySelectorAll("button").forEach((b) => (b.onclick = () => {
    const k = b.dataset.k;
    const next = state.target.includes(k) ? state.target.filter((x) => x !== k) : [...state.target, k];
    if (!next.length) return toast("Keep at least one target class");
    state.target = next;
    changed(true);
  }));
  $("#include-alt").checked = state.include_alt;
  $("#all-sources").checked = state.all_sources;
  document.querySelectorAll("#truth button").forEach((b) => b.classList.toggle("on", b.dataset.v === state.truth));
}

function renderPipeline() {
  const el = $("#pipe");
  const rules = Object.fromEntries(CFG.rules.map((r) => [r.name, r]));
  const saved = SAVED.find((s) => s.name === state.name);
  const edited = saved && !sameSteps(saved.config, requestFor().pipeline);
  el.innerHTML = `
    <div class="card-head">
      <h2 class="pipe-title">Pipeline ${saved ? `<span class="muted">· ${esc(saved.name)}${edited ? " (edited)" : ""}</span>` : ""}</h2>
      <span>
        <select class="small" data-act="load" aria-label="Load a saved filter"><option value="">Load saved…</option>
          ${SAVED.map((s, i) => `<option value="${i}">${esc(s.name)}</option>`).join("")}</select>
        <button class="small" data-act="save">Save…</button>
        <button class="small" data-act="reset">Reset</button></span>
    </div>
    <div class="rules">${state.pipeline.map((s, i) => `
      <div class="rule ${s.on ? "" : "off"}">
        <input type="checkbox" data-i="${i}" ${s.on ? "checked" : ""} aria-label="enable ${esc(s.rule)}">
        <span class="name">${esc(s.rule)}${rules[s.rule].broker ? '<span class="tag" title="reads broker output">broker</span>' : ""}</span>
        <span class="params">${Object.entries(s.params).map(([k, v]) =>
          `<label>${esc(k)} <input type="number" step="${INTEGER_PARAMS.has(k) ? 1 : "any"}" data-i="${i}" data-p="${esc(k)}" value="${v}"></label>`).join("")}</span>
        <span class="desc">${esc(rules[s.rule].description)}</span>
      </div>`).join("")}</div>`;
  el.querySelectorAll('input[type=checkbox]').forEach((c) => (c.onchange = () => {
    state.pipeline[+c.dataset.i].on = c.checked;
    renderPipeline();
    changed();
  }));
  el.querySelectorAll("input[data-p]").forEach((inp) => (inp.oninput = () => {
    const v = Number(inp.value);
    if (inp.value === "" || !Number.isFinite(v)) return;
    state.pipeline[+inp.dataset.i].params[inp.dataset.p] = v;
    refreshTitle();
    changed();
  }));
  el.querySelector('[data-act=load]').onchange = (e) => e.target.value && loadSavedFilter(SAVED[+e.target.value]);
  el.querySelector('[data-act=save]').onclick = async () => {
    const name = prompt("Save this pipeline as:", state.name || "");
    if (!name?.trim()) return;
    try {
      if (!(await savePipeline(name.trim(), requestFor().pipeline, saved?.name === name.trim() ? saved.note : null))) return;
    } catch (e) { return toast("Save failed: " + e.message, 5000); }
    state.name = name.trim();
    saveState();
    renderPipeline();
  };
  el.querySelector('[data-act=reset]').onclick = () => {
    state.pipeline = pipelineFrom(CFG.default_pipeline);
    state.name = null;
    renderPipeline();
    changed();
  };
}

// Update only the title's "(edited)" mark, so typing in a threshold box keeps its focus.
function refreshTitle() {
  const saved = SAVED.find((s) => s.name === state.name);
  const tag = $("#pipe .pipe-title .muted");
  if (saved && tag) tag.textContent = `· ${saved.name}${sameSteps(saved.config, requestFor().pipeline) ? "" : " (edited)"}`;
}

// ---------- evaluation ----------

let debounce = null;
function changed(settings = false) {
  saveState();
  if (settings) renderSettings();
  renderSweepParams();
  clearTimeout(debounce);
  debounce = setTimeout(evaluatePipeline, 200);
}

async function evaluatePipeline() {
  const token = ++evalToken;
  try {
    const r = await api("/api/rejection/evaluate", requestFor());
    if (token !== evalToken) return; // a newer request superseded this one
    result = r;
    $("#err").textContent = "";
    renderAll();
  } catch (e) {
    if (token === evalToken) $("#err").textContent = e.message;
  }
}

function renderAll() {
  const p = result.population;
  const excl = Object.entries(p.excluded).map(([k, n]) => `${n} ${k}`).join(", ");
  $("#pop").innerHTML = `<b>${p.scored}</b> scored objects, <b>${p.n_target}</b> target` + (excl ? ` · excluded ${esc(excl)}` : "") +
    (p.n_target < 30 ? ` · <span class="err">few targets: wide intervals</span>` : "");
  renderMetrics();
  renderTrees();
  renderByClass();
  renderSteps();
  drawSweepMarker();
}

function cell(m) {
  if (!m || m.value === null) return `<td class="num">— <span class="ci">(${m ? `${m.k}/${m.n}` : ""})</span></td>`;
  return `<td class="num">${pct(m.value)} <span class="ci">[${pct(m.ci95[0])}, ${pct(m.ci95[1])}] (${m.k}/${m.n})</span></td>`;
}

function renderMetrics() {
  const rows = METRICS.map((mt) => {
    const base = result.baseline[mt.key], m = result.pipeline_metrics[mt.key];
    const d = base.value !== null && m.value !== null ? m.value - base.value : null;
    const cls = d === null || Math.abs(d) < 1e-9 ? "" : (d > 0) === mt.higher ? "better" : "worse";
    return `<tr><th title="${esc(mt.hint)}">${mt.label} <span class="ci">${esc(mt.hint)}</span></th>${cell(base)}${cell(m)}
      <td class="num ${cls}">${d === null ? "—" : (d >= 0 ? "+" : "") + d.toFixed(3)}</td></tr>`;
  });
  const kept = result.pipeline_metrics.kept, all = result.baseline.kept;
  rows.push(`<tr><th>Kept</th><td class="num">${all}</td><td class="num">${kept}</td><td class="num">${kept - all}</td></tr>`);
  $("#metrics").innerHTML = `<tr><th></th><th class="num">No filter</th><th class="num">Pipeline</th><th class="num">change</th></tr>${rows.join("")}`;
}

function classCounts(res) {
  const out = {};
  for (const o of res.objects) {
    out[o.truth] ??= { total: 0, kept: 0, target: 0 };
    out[o.truth].total++;
    if (o.kept) out[o.truth].kept++;
    if (o.target) out[o.truth].target++;
  }
  return out;
}

function plotLayout(extra = {}) {
  const ink = cssVar("--muted"), line = cssVar("--line");
  return {
    margin: { l: 50, r: 16, t: 10, b: 40 },
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: "system-ui, sans-serif", size: 12, color: ink },
    legend: { orientation: "h", y: 1.12, x: 0 },
    xaxis: { gridcolor: line, zerolinecolor: line, linecolor: line },
    yaxis: { gridcolor: line, zerolinecolor: line, linecolor: line },
    hoverlabel: { bgcolor: cssVar("--card"), bordercolor: line, font: { color: cssVar("--ink") } },
    ...extra,
  };
}

function renderByClass() {
  const a = classCounts(result);
  const classes = CFG.classes.map((c) => c.key).filter((k) => a[k]);
  const label = (k) => k + (a[k].target ? (a[k].target === a[k].total ? " ★" : " ☆") : "");
  const x = classes.map(label);
  const bar = (name, ys, color) => ({
    type: "bar", name, x, y: ys, marker: { color, line: { color: cssVar("--card"), width: 2 } },
    hovertemplate: `${name}: %{y}<extra>%{x}</extra>`,
  });
  Plotly.react("by-class", [
    bar("No filter", classes.map((k) => a[k].total), cssVar("--baseline")),
    bar("Kept", classes.map((k) => a[k].kept), cssVar("--series-1")),
  ], plotLayout({ barmode: "group", bargap: 0.3, bargroupgap: 0.08, yaxis: { title: "objects", gridcolor: cssVar("--line"), rangemode: "tozero" } }),
  { displayModeBar: false, responsive: true });
  $("#by-class-table").innerHTML = `<tr><th>class</th><th class="num">labeled</th><th class="num">of which target</th><th class="num">kept</th><th class="num">rejected</th></tr>` +
    classes.map((k) => `<tr><td>${esc(k)}</td><td class="num">${a[k].total}</td><td class="num">${a[k].target}</td><td class="num">${a[k].kept}</td><td class="num">${a[k].total - a[k].kept}</td></tr>`).join("") +
    `<tr><td colspan="5" class="hint">★ all of the class is target, ☆ part of it (a subclass or could-also-be target)</td></tr>`;
}

function renderSteps() {
  const nT = result.population.n_target, nN = result.population.scored - nT;
  const rows = Object.entries(result.steps).map(([step, st]) => {
    const lostT = nT - st.alone.completeness.k, cutN = nN - st.alone.fpr.k;
    const only = Object.entries(st.unique_rejects).filter(([, n]) => n).map(([c, n]) => `${c} ${n}`).join(", ") || "—";
    return `<tr><td>${esc(step)}${st.broker ? '<span class="tag">broker</span>' : ""}</td>
      <td class="num">${st.no_data}</td><td class="num">${lostT}</td><td class="num">${cutN}</td><td>${esc(only)}</td>
      ${cell(st.without_this_step.completeness)}${cell(st.without_this_step.purity)}</tr>`;
  });
  $("#steps").innerHTML = `<tr><th>step</th><th class="num">no data</th><th class="num">target lost alone</th><th class="num">non-target cut alone</th>
    <th>only it rejects</th><th class="num">completeness without</th><th class="num">purity without</th></tr>${rows.join("")}`;
}

// ---------- sweep ----------

function sweepOptions() {
  const out = [];
  state.pipeline.forEach((s) => {
    if (!s.on) return;
    for (const p of Object.keys(s.params)) out.push({ value: `${s.rule}:${p}`, label: `${s.rule} · ${p}`, rule: s.rule, param: p });
  });
  return out;
}

function renderSweepParams() {
  const sel = $("#sweep-param");
  const prev = sel.value;
  const opts = sweepOptions();
  sel.innerHTML = opts.map((o) => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
  if (opts.some((o) => o.value === prev)) sel.value = prev;
  else setSweepRange();
}

function setSweepRange() {
  const [, param] = ($("#sweep-param").value || ":").split(":");
  const [lo, hi] = SWEEP_RANGE[param] || [0, 1];
  $("#sweep-from").value = lo;
  $("#sweep-to").value = hi;
  $("#sweep-n").value = INTEGER_PARAMS.has(param) ? Math.min(hi - lo + 1, 60) : 20;
}

let lastSweep = null;

async function runSweep() {
  const [rule, param] = ($("#sweep-param").value || ":").split(":");
  if (!rule) return toast("Enable a rule with a threshold first");
  const body = requestFor();
  const step = body.pipeline.findIndex((s) => s.rule === rule);
  const lo = Number($("#sweep-from").value), hi = Number($("#sweep-to").value), n = Math.max(2, Math.min(60, Number($("#sweep-n").value)));
  let values = Array.from({ length: n }, (_, i) => lo + (hi - lo) * i / (n - 1));
  if (INTEGER_PARAMS.has(param)) values = [...new Set(values.map(Math.round))];
  try {
    const pts = await api("/api/rejection/sweep", { ...body, step, param, values });
    lastSweep = { rule, param, pts };
    drawSweep();
  } catch (e) {
    toast("Sweep failed: " + e.message, 5000);
  }
}

function drawSweep() {
  if (!lastSweep) return;
  const { rule, param, pts } = lastSweep;
  const x = pts.map((p) => p.value);
  const line = (key, name, color) => ({
    type: "scatter", mode: "lines+markers", name, x,
    y: pts.map((p) => p[key].value),
    customdata: pts.map((p) => [p[key].ci95[0], p[key].ci95[1], p[key].k, p[key].n, p.kept]),
    line: { color, width: 2 }, marker: { color, size: 8, line: { color: cssVar("--card"), width: 2 } },
    hovertemplate: `${name} %{y:.3f} [%{customdata[0]:.3f}, %{customdata[1]:.3f}] (%{customdata[2]}/%{customdata[3]}) · kept %{customdata[4]}<extra></extra>`,
  });
  Plotly.react("sweep", [
    line("completeness", "Completeness", cssVar("--series-1")),
    line("purity", "Purity", cssVar("--series-2")),
    line("fpr", "False-positive rate", cssVar("--series-3")),
  ], plotLayout({
    hovermode: "x unified",
    xaxis: { title: `${rule} · ${param}`, gridcolor: cssVar("--line") },
    yaxis: { range: [0, 1.02], gridcolor: cssVar("--line") },
    shapes: markerShape(),
  }), { displayModeBar: false, responsive: true });
  const el = $("#sweep");
  el.removeAllListeners?.("plotly_click");
  el.on("plotly_click", (ev) => {
    const v = ev.points[0].x;
    const s = state.pipeline.find((s) => s.rule === rule);
    if (!s) return;
    s.params[param] = INTEGER_PARAMS.has(param) ? Math.round(v) : +v.toPrecision(4);
    renderPipeline();
    changed();
    toast(`${rule} ${param} = ${s.params[param]}`);
  });
}

function markerShape() {
  if (!lastSweep) return [];
  const s = state.pipeline.find((s) => s.rule === lastSweep.rule && s.on);
  if (!s) return [];
  const v = s.params[lastSweep.param];
  return [{ type: "line", x0: v, x1: v, y0: 0, y1: 1.02, line: { color: cssVar("--muted"), width: 1.5, dash: "dash" } }];
}

function drawSweepMarker() {
  if (lastSweep && $("#sweep").data) Plotly.relayout("sweep", { shapes: markerShape() });
}

// ---------- questions ----------

// Plain-language wording per rule, for the decision tree (node) and the questionnaire (ask, why, choices).
// A choice's params null leaves the rule out. Rules missing here get a generic yes/no question.
const arcsec = (v) => `${v}″`;
const QUESTIONS = {
  sso_association: {
    node: () => "Known solar-system object?",
    ask: "Drop objects the pipeline already matched to a known asteroid or comet?",
    why: "Rubin ssObjectId or ZTF ssnamenr on any detection. Movers are real but not transients.",
    choices: [{ label: "Yes, drop them", params: {} }, { label: "No, keep them", params: null }],
  },
  gaia_star: {
    node: (p) => `Gaia star within ${arcsec(p.max_sep)}?`,
    ask: "Drop objects sitting on a Gaia star?",
    why: "A Gaia DR3 source with significant parallax or proper motion. A wider radius drops more variable stars, and more transients near stars.",
    choices: [{ label: "Yes, within 2″", params: { max_sep: 2 } }, { label: "Yes, within 1″", params: { max_sep: 1 } },
      { label: "Yes, within 0.5″", params: { max_sep: 0.5 } }, { label: "No", params: null }],
  },
  milliquas_agn: {
    node: (p) => `Milliquas AGN/QSO within ${arcsec(p.max_sep)}?`,
    ask: "Drop objects on a known AGN or quasar?",
    why: "A Milliquas QSO/AGN nearby. Answer no if AGN flares or nuclear transients are what you want.",
    choices: [{ label: "Yes, within 2″", params: { max_sep: 2 } }, { label: "Yes, within 1″", params: { max_sep: 1 } },
      { label: "Yes, within 0.5″", params: { max_sep: 0.5 } }, { label: "No", params: null }],
  },
  galactic_plane: {
    node: (p) => `Within ${p.min_b}° of the Galactic plane?`,
    ask: "Avoid the crowded Galactic plane?",
    why: "Low |b| fields are full of variable stars and crowding artifacts.",
    choices: [{ label: "Yes, |b| < 15°", params: { min_b: 15 } }, { label: "Yes, |b| < 10°", params: { min_b: 10 } },
      { label: "Yes, |b| < 5°", params: { min_b: 5 } }, { label: "No", params: null }],
  },
  ecliptic: {
    node: (p) => `Within ${p.min_beta}° of the ecliptic?`,
    ask: "Avoid the ecliptic, where most asteroids are?",
    why: "Ecliptic latitude |β|. Unknown asteroids that the pipeline didn't associate pile up near the ecliptic.",
    choices: [{ label: "Yes, |β| < 15°", params: { min_beta: 15 } }, { label: "Yes, |β| < 10°", params: { min_beta: 10 } },
      { label: "Yes, |β| < 5°", params: { min_beta: 5 } }, { label: "No", params: null }],
  },
  negative_flux: {
    node: (p) => `More than ${Math.round(p.max_frac * 100)}% negative detections?`,
    ask: "Drop objects whose LSST detections are mostly negative difference flux?",
    why: "Mostly negative flux usually means a subtraction artifact or a source fading below its template.",
    choices: [{ label: "Yes, over 30%", params: { max_frac: 0.3 } }, { label: "Yes, over 50%", params: { max_frac: 0.5 } },
      { label: "Yes, over 80%", params: { max_frac: 0.8 } }, { label: "No", params: null }],
  },
  position_scatter: {
    node: (p) => `Positions scatter more than ${arcsec(p.max_rms)}?`,
    ask: "Drop objects whose detections wander on the sky?",
    why: "rms of LSST detection positions about their mean. Large scatter means a mover or a bad subtraction.",
    choices: [{ label: "Yes, over 0.3″", params: { max_rms: 0.3 } }, { label: "Yes, over 0.5″", params: { max_rms: 0.5 } },
      { label: "Yes, over 1″", params: { max_rms: 1 } }, { label: "No", params: null }],
  },
  min_detections: {
    node: (p) => `Fewer than ${p.n} detections?`,
    ask: "How many detections (LSST + ZTF) do you need?",
    why: "Upper limits don't count.",
    choices: [{ label: "At least 2", params: { n: 2 } }, { label: "At least 3", params: { n: 3 } },
      { label: "At least 5", params: { n: 5 } }, { label: "Any number", params: null }],
  },
  min_lsst_detections: {
    node: (p) => `Fewer than ${p.n} LSST detections?`,
    ask: "How many LSST detections do you need?",
    why: "Like the last question, but ZTF detections don't count.",
    choices: [{ label: "At least 2", params: { n: 2 } }, { label: "At least 3", params: { n: 3 } },
      { label: "At least 5", params: { n: 5 } }, { label: "Any number", params: null }],
  },
  min_nights: {
    node: (p) => `Seen on fewer than ${p.n} nights?`,
    ask: "Must an object be detected on more than one night?",
    why: "One-night objects are mostly artifacts and asteroids, but young transients start out as one-night objects too.",
    choices: [{ label: "Yes, 2+ nights", params: { n: 2 } }, { label: "Yes, 3+ nights", params: { n: 3 } }, { label: "No", params: null }],
  },
  min_span: {
    node: (p) => `Detections span less than ${p.days} d?`,
    ask: "Must the detections span some time?",
    why: "Days from first to last detection.",
    choices: [{ label: "At least 1 day", params: { days: 1 } }, { label: "At least 3 days", params: { days: 3 } },
      { label: "At least 10 days", params: { days: 10 } }, { label: "No", params: null }],
  },
  extended_diff: {
    node: (p) => `Difference image extended (median > ${p.max_ext})?`,
    ask: "Drop objects whose difference-image detections look extended?",
    why: "Rubin's extendedness of each diaSource (0 point-like, 1 extended). A real new point source is point-like in the difference image; extended residuals are often bad subtractions of galaxies or bright stars.",
    choices: [{ label: "Yes, median > 0.5", params: { max_ext: 0.5 } }, { label: "Yes, median > 0.9", params: { max_ext: 0.9 } }, { label: "No", params: null }],
  },
  dipole: {
    node: (p) => `More than ${Math.round(p.max_frac * 100)}% dipoles?`,
    ask: "Drop objects whose detections are mostly dipoles?",
    why: "Rubin flags a diaSource as a dipole when it looks like a positive and negative lobe side by side: usually misalignment or a mismatched PSF in the subtraction.",
    choices: [{ label: "Yes, over 30%", params: { max_frac: 0.3 } }, { label: "Yes, over 50%", params: { max_frac: 0.5 } }, { label: "No", params: null }],
  },
  glint: {
    node: () => "Satellite glint?",
    ask: "Drop objects with a detection flagged as a satellite glint?",
    why: "Rubin's glint_trail flag: the detection lies on a trail of glints from a satellite.",
    choices: [{ label: "Yes, drop them", params: {} }, { label: "No, keep them", params: null }],
  },
  bad_psf_fit: {
    node: (p) => `PSF fit χ²/N above ${p.max_chi2}?`,
    ask: "Drop objects that don't look like the PSF?",
    why: "Median reduced χ² of Rubin's PSF fits to the difference-image detections. A point source that matches the PSF fits near 1.",
    choices: [{ label: "Yes, χ²/N > 2", params: { max_chi2: 2 } }, { label: "Yes, χ²/N > 5", params: { max_chi2: 5 } },
      { label: "Yes, χ²/N > 10", params: { max_chi2: 10 } }, { label: "No", params: null }],
  },
  sharp: {
    node: (p) => `Sharper than the PSF (size ratio < ${p.min_ratio})?`,
    ask: "Drop detections much sharper than a point source?",
    why: "Second moments of the detection relative to the PSF's. Much smaller than the PSF suggests a cosmic ray or hot pixel. Check where the population sits with a sweep: these moments run below 1 for many detections.",
    choices: [{ label: "Yes, under 0.25×", params: { min_ratio: 0.25 } }, { label: "Yes, under 0.1×", params: { min_ratio: 0.1 } }, { label: "No", params: null }],
  },
  oversized: {
    node: (p) => `Larger than ${p.max_ratio}× the PSF?`,
    ask: "Drop detections much larger than a point source?",
    why: "Second moments of the detection relative to the PSF's. A point source is about the PSF's size; extended residuals spread out.",
    choices: [{ label: "Yes, over 1.5×", params: { max_ratio: 1.5 } }, { label: "Yes, over 2×", params: { max_ratio: 2 } },
      { label: "Yes, over 3×", params: { max_ratio: 3 } }, { label: "No", params: null }],
  },
  measurement_flags: {
    node: (p) => `More than ${Math.round(p.max_frac * 100)}% failed measurements?`,
    ask: "Drop objects whose measurements mostly failed?",
    why: "Rubin's PSF flux, centroid, shape or aperture flux flags: the pipeline couldn't measure the detection properly.",
    choices: [{ label: "Yes, over 50%", params: { max_frac: 0.5 } }, { label: "Yes, all of them", params: { max_frac: 0.99 } }, { label: "No", params: null }],
  },
  low_snr: {
    node: (p) => `Brightest detection SNR below ${p.min_snr}?`,
    ask: "How significant does the best detection have to be?",
    why: "Alerts start at 5σ, where many are noise. This uses the highest SNR of any LSST detection.",
    choices: [{ label: "SNR ≥ 6", params: { min_snr: 6 } }, { label: "SNR ≥ 7", params: { min_snr: 7 } },
      { label: "SNR ≥ 10", params: { min_snr: 10 } }, { label: "Any", params: null }],
  },
  trailed: {
    node: (p) => `Trailed more than ${arcsec(p.max_len)}?`,
    ask: "Drop detections that are trailed, like a fast-moving asteroid?",
    why: "Rubin's fitted trail length. Fast movers smear into a streak within one exposure.",
    choices: [{ label: "Yes, over 1.5″", params: { max_len: 1.5 } }, { label: "Yes, over 2″", params: { max_len: 2 } },
      { label: "Yes, over 4″", params: { max_len: 4 } }, { label: "No", params: null }],
  },
  ztf_ps1_star: {
    node: (p) => `ZTF: PS1 star within ${arcsec(p.max_dist)} (sgscore ≥ ${p.min_sg})?`,
    ask: "Drop objects ZTF puts on a Pan-STARRS1 star?",
    why: "From ZTF alerts: the nearest PS1 source and its star/galaxy score (1 star). Only objects with ZTF detections have it.",
    choices: [{ label: "Yes, within 1.5″, likely star", params: { max_dist: 1.5, min_sg: 0.5 } },
      { label: "Yes, within 1″, likely star", params: { max_dist: 1, min_sg: 0.5 } },
      { label: "Yes, within 1″, surely star", params: { max_dist: 1, min_sg: 0.8 } }, { label: "No", params: null }],
  },
  antares_bright_star: {
    node: () => "ANTARES: near a bright guide star?",
    ask: "Drop objects ANTARES matched to a bright star?",
    why: "ANTARES's bright guide star catalog match. Bright stars cause ghosts, spikes and bad subtractions nearby. ANTARES doesn't report the separation, so this is yes or no.",
    choices: [{ label: "Yes, drop them", params: {} }, { label: "No, keep them", params: null }],
  },
  antares_gaia_variable: {
    node: () => "ANTARES: Gaia variable?",
    ask: "Drop objects ANTARES matched to a known Gaia variable?",
    why: "ANTARES's match to the Gaia DR3 variability catalog.",
    choices: [{ label: "Yes, drop them", params: {} }, { label: "No, keep them", params: null }],
  },
  scimma_quality: {
    node: () => "Not tagged quality transient by SCIMMA?",
    ask: "Require ANTARES's SCIMMA quality-transient tag?",
    why: "Broker output: an ANTARES filter's tag. Untagged objects are dropped, including ones the filter never saw.",
    choices: [{ label: "Yes, require it", params: {} }, { label: "No", params: null }],
  },
  lsst_reliability: {
    node: (p) => `LSST reliability below ${p.min_rel}?`,
    ask: "Use Rubin's reliability score?",
    why: "Broker output: drops objects whose best per-alert reliability is low. Your revealed labels may have seen it.",
    choices: [{ label: "Yes, need ≥ 0.7", params: { min_rel: 0.7 } }, { label: "Yes, need ≥ 0.5", params: { min_rel: 0.5 } },
      { label: "Yes, need ≥ 0.3", params: { min_rel: 0.3 } }, { label: "No", params: null }],
  },
  alerce_stamp_bogus: {
    node: (p) => `ALeRCE P(bogus) above ${p.max_p}?`,
    ask: "Use ALeRCE's stamp classifier?",
    why: "Broker output: drops objects ALeRCE's LSST stamp classifier calls bogus.",
    choices: [{ label: "Yes, P(bogus) > 0.3", params: { max_p: 0.3 } }, { label: "Yes, P(bogus) > 0.5", params: { max_p: 0.5 } },
      { label: "Yes, P(bogus) > 0.8", params: { max_p: 0.8 } }, { label: "No", params: null }],
  },
};
// Questionnaire order, which is also the built pipeline's order: catalog facts, then data quality, then broker scores.
const QUESTION_ORDER = ["sso_association", "trailed", "glint", "gaia_star", "antares_bright_star", "antares_gaia_variable", "ztf_ps1_star",
  "milliquas_agn", "galactic_plane", "ecliptic", "negative_flux", "dipole", "extended_diff", "bad_psf_fit", "sharp", "oversized", "measurement_flags",
  "low_snr", "position_scatter", "min_detections", "min_lsst_detections", "min_nights", "min_span",
  "lsst_reliability", "alerce_stamp_bogus", "scimma_quality"];

function questionFor(name) {
  const rule = CFG.rules.find((r) => r.name === name);
  return QUESTIONS[name] || {
    node: (p) => `${name}${Object.keys(p).length ? ` (${Object.entries(p).map(([k, v]) => `${k} ${v}`).join(", ")})` : ""}?`,
    ask: `Use the ${name} cut?`,
    why: rule.description,
    choices: [{ label: "Yes, at the defaults", params: { ...rule.defaults } }, { label: "No", params: null }],
  };
}

const nodeText = (step) => questionFor(step.rule).node({ ...CFG.rules.find((r) => r.name === step.rule).defaults, ...step });
const stepParams = ({ rule, ...params }) => params;
const sameSteps = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// ---------- decision tree ----------

// The pipeline as a cascade: each object falls to the first question it answers "yes" to.
// Built from the evaluate response, so counts match the metrics exactly.
function treeHTML(res, title) {
  const keys = Object.keys(res.steps);
  const total = res.objects.length;
  const steps = res.pipeline;
  const groups = { kept: res.objects.filter((o) => o.kept) };
  let reach = res.objects;
  const rows = keys.map((k, i) => {
    const rej = reach.filter((o) => o.rejected_by === k);
    const noData = reach.filter((o) => o[`step:${k}`] === null).length;
    groups[i] = rej;
    const row = `
      <div class="t-edge">${i ? `no · ${reach.length}` : ""}</div>
      <div class="t-row">
        <div class="t-node q"><div class="t-q">${esc(nodeText(steps[i]))}${res.steps[k].broker ? '<span class="tag">broker</span>' : ""}</div>
          ${tBar(reach, total)}${noData ? `<div class="hint">${noData} without data pass</div>` : ""}</div>
        <div class="t-yes">yes</div>
        <button class="t-node leaf rej" data-g="${i}" ${rej.length ? "" : "disabled"}><b>${rej.length}</b> rejected${tBar(rej, total)}
          <div class="hint">${esc(classSummary(rej))}</div></button>
      </div>`;
    reach = reach.filter((o) => o.rejected_by !== k);
    return row;
  });
  return { groups, html: `
    <div class="tree">
      <div class="t-head">${title}</div>
      <div class="t-node root"><b>${total}</b> scored objects${tBar(res.objects, total)}</div>
      ${rows.join("") || '<div class="t-edge"></div><div class="hint">No questions: everything is kept.</div>'}
      <div class="t-edge">${keys.length ? `no · ${reach.length}` : ""}</div>
      <button class="t-node leaf kept" data-g="kept"><b>${groups.kept.length}</b> kept${tBar(groups.kept, total)}
        <div class="hint">${esc(classSummary(groups.kept))}</div></button>
      <div class="t-list"></div>
    </div>` };
}

function tBar(objs, total) {
  const t = objs.filter((o) => o.target).length, n = objs.length - t;
  const w = (k) => (total ? (100 * k) / total : 0).toFixed(1);
  return `<div class="t-bar" title="${t} target · ${n} other"><i class="t" style="width:${w(t)}%"></i><i class="o" style="width:${w(n)}%"></i></div>
    <div class="t-cap">${t} target · ${n} other</div>`;
}

function classSummary(objs) {
  const c = {};
  for (const o of objs) c[o.truth] = (c[o.truth] || 0) + 1;
  return Object.entries(c).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${k} ${n}`).join(" · ");
}

function objectList(objs) {
  const sorted = [...objs].sort((x, y) => (y.target - x.target) || x.truth.localeCompare(y.truth));
  const shown = sorted.slice(0, 200);
  return `<table><tr><th>object</th><th>label</th><th>target</th></tr>${shown.map((o) => `<tr>
    <td><a href="/vet?object=${encodeURIComponent(o.object_id)}" target="_blank">${esc(o.object_id)}</a></td>
    <td>${esc(o.truth + (o.subclass ? ` · ${o.subclass}` : "") + (o.alt_classes ? ` (or ${o.alt_classes})` : ""))}</td>
    <td>${o.target ? "✓" : ""}</td></tr>`).join("")}</table>` +
    (sorted.length > shown.length ? `<div class="hint">and ${sorted.length - shown.length} more</div>` : "");
}

function renderTree(el, res, title) {
  const { html, groups } = treeHTML(res, title);
  el.innerHTML = html;
  const list = el.querySelector(".t-list");
  el.querySelectorAll(".leaf").forEach((b) => (b.onclick = () => {
    const open = b.classList.contains("open");
    el.querySelectorAll(".leaf.open").forEach((x) => x.classList.remove("open"));
    list.innerHTML = open ? "" : objectList(groups[b.dataset.g]);
    b.classList.toggle("open", !open);
  }));
}

function renderTrees() {
  renderTree($("#tree"), result, `Pipeline${state.name ? ` · ${esc(state.name)}` : ""}`);
}

// ---------- saved filters ----------

let SAVED = [];

async function loadSaved() {
  SAVED = await api("/api/rejection/saved");
  renderSaved();
  renderPipeline();
}

function stepChip(step) {
  const p = stepParams(step);
  return `<span class="pill" title="${esc(nodeText(step))}">${esc(step.rule)}${Object.keys(p).length ? " " + esc(Object.values(p).join(", ")) : ""}</span>`;
}

function renderSaved() {
  $("#saved").innerHTML = SAVED.length
    ? `<tr><th>name</th><th>questions, in order</th><th>note</th><th>by</th><th>saved</th><th></th></tr>` + SAVED.map((s, i) => `<tr>
        <td><b>${esc(s.name)}</b></td>
        <td class="wrap">${s.config.map(stepChip).join("") || '<span class="hint">none: keeps everything</span>'}</td>
        <td class="wrap hint">${esc(s.note || "")}</td>
        <td class="hint">${esc(s.owner || "—")}</td>
        <td class="hint">${esc(s.updated_at.slice(0, 16).replace("T", " "))}</td>
        <td><button class="small" data-i="${i}" data-load>Load</button>
          ${s.mine ? `<button class="small" data-i="${i}" data-del title="Delete">✕</button>` : ""}</td></tr>`).join("")
    : `<tr><td class="hint wrap">Nothing saved yet. Save a pipeline from its card, or build one with questions.</td></tr>`;
  $("#saved").querySelectorAll("button[data-load]").forEach((b) => (b.onclick = () => loadSavedFilter(SAVED[+b.dataset.i])));
  $("#saved").querySelectorAll("button[data-del]").forEach((b) => (b.onclick = async () => {
    const s = SAVED[+b.dataset.i];
    if (!confirm(`Delete saved filter "${s.name}"?`)) return;
    try {
      const r = await fetch(`/api/rejection/saved/${encodeURIComponent(s.name)}`, { method: "DELETE" });
      if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
      if (state.name === s.name) state.name = null;
      saveState();
      await loadSaved();
      toast(`Deleted "${s.name}"`);
    } catch (e) { toast("Delete failed: " + e.message, 5000); }
  }));
}

function loadSavedFilter(saved) {
  state.pipeline = pipelineFrom(saved.config);
  state.name = saved.name;
  renderPipeline();
  changed();
  toast(`Loaded "${saved.name}"`);
}

async function savePipeline(name, pipeline, note) {
  const same = SAVED.find((s) => s.name === name);
  if (same && !same.mine) throw new Error(`"${name}" is ${same.owner}'s filter; save yours under another name`);
  if (same && !confirm(`Overwrite saved filter "${name}"?`)) return false;
  await api("/api/rejection/saved", { name, pipeline, note });
  await loadSaved();
  toast(`Saved "${name}"`);
  return true;
}

// ---------- questionnaire ----------

// wiz.at: -1 is the target question; 0.. index into wiz.questions. wiz.answers[i] is a choice index.
let wiz = null;
let wizToken = 0;

function startWizard() {
  const names = CFG.rules.map((r) => r.name);
  wiz = { at: -1, answers: [], questions: [...QUESTION_ORDER.filter((n) => names.includes(n)), ...names.filter((n) => !QUESTION_ORDER.includes(n))] };
  $("#wizard").hidden = false;
  $("#wiz-start").textContent = "Restart questions";
  renderWizard();
}

function closeWizard() {
  wiz = null;
  $("#wizard").hidden = true;
  $("#wizard").innerHTML = "";
  $("#wiz-start").textContent = "Build a filter with questions";
}

const wizPipeline = (upto = wiz.answers.length) => wiz.answers.slice(0, upto).flatMap((c, i) => {
  const params = questionFor(wiz.questions[i]).choices[c].params;
  return params ? [{ rule: wiz.questions[i], ...params }] : [];
});

const evalReq = (pipeline) => ({ ...requestFor(), pipeline });

function wizNote() {
  return wiz.questions.slice(0, wiz.answers.length)
    .map((n, i) => ({ n, c: questionFor(n).choices[wiz.answers[i]] }))
    .filter(({ c }) => c.params).map(({ n, c }) => `${n}: ${c.label.replace(/^Yes, /, "")}`).join("; ");
}

function renderWizard() {
  const el = $("#wizard");
  const token = ++wizToken;
  const n = wiz.questions.length;
  const nav = `<div class="wiz-nav hint">
    ${wiz.at >= 0 ? `<button class="small" data-act="back">← Back</button>` : ""}
    ${wiz.at >= 0 && wiz.at < n ? `Question ${wiz.at + 1} of ${n}` : wiz.at < 0 ? "First, the goal" : "Done"}
    <span class="wiz-progress"><i style="width:${(100 * Math.max(0, wiz.at)) / n}%"></i></span>
    ${wiz.at >= 0 && wiz.at < n ? `<button class="small" data-act="finish" title="Answer no to every remaining question">Finish here</button>` : ""}
    <button class="small" data-act="close">Close</button></div>`;
  let body;
  if (wiz.at < 0) {
    body = `<div class="wiz-ask">What do you want the filter to keep?</div>
      <p class="hint">Objects labeled with any of these classes count as targets. Every later answer shows how many targets and others it would throw away. This is the same as "Keep" in the header.</p>
      <div class="chips wiz-targets">${targetList().map((t) => `<button data-k="${t.key}" class="${state.target.includes(t.key) ? "on" : ""}">${esc(t.label)}</button>`).join("")}</div>
      <p><button class="primary" data-act="next">Next →</button></p>`;
  } else if (wiz.at < n) {
    const q = questionFor(wiz.questions[wiz.at]);
    const rule = CFG.rules.find((r) => r.name === wiz.questions[wiz.at]);
    body = `<div class="wiz-ask">${esc(q.ask)}${rule.broker ? '<span class="tag">broker</span>' : ""}</div>
      <p class="hint">${esc(q.why)}</p>
      <div class="wiz-choices">${q.choices.map((c, i) => `
        <button class="choice ${wiz.answers[wiz.at] === i ? "on" : ""}" data-c="${i}">
          <span class="c-label">${esc(c.label)}${sameSteps(c.params, rule.defaults) ? ' <span class="tag">default</span>' : ""}</span>
          <span class="impact" data-c="${i}">${c.params ? "…" : "keeps everything that got here"}</span></button>`).join("")}</div>`;
  } else {
    const pipeline = wizPipeline();
    body = `<div class="wiz-ask">Your filter asks ${pipeline.length} question${pipeline.length === 1 ? "" : "s"}.</div>
      <div class="wiz-summary">${pipeline.map(stepChip).join("") || '<span class="hint">none: it keeps everything</span>'}</div>
      <div class="wiz-save">
        <label>Name <input id="wiz-name" maxlength="80" placeholder="e.g. strict transients"></label>
        <label class="grow">Note <input id="wiz-note" value="${esc(wizNote())}"></label>
      </div>
      <p><button class="primary" data-act="save" data-load>Save and use it</button>
        <button data-act="save">Just save</button></p>`;
  }
  el.innerHTML = `<div class="wiz"><div class="wiz-main">${nav}${body}</div><div class="wiz-tree"><div class="hint">building…</div></div></div>`;

  el.querySelector("[data-act=close]").onclick = closeWizard;
  el.querySelector("[data-act=back]")?.addEventListener("click", () => { wiz.at--; renderWizard(); });
  el.querySelector("[data-act=next]")?.addEventListener("click", () => { wiz.at = 0; renderWizard(); });
  el.querySelector("[data-act=finish]")?.addEventListener("click", () => {
    // leave out every rule not yet answered (each question's "no" is its param-less choice)
    for (let i = wiz.at; i < wiz.questions.length; i++) wiz.answers[i] = questionFor(wiz.questions[i]).choices.findIndex((c) => !c.params);
    wiz.at = wiz.questions.length;
    renderWizard();
  });
  el.querySelectorAll(".wiz-targets button").forEach((b) => (b.onclick = () => {
    const k = b.dataset.k;
    const next = state.target.includes(k) ? state.target.filter((x) => x !== k) : [...state.target, k];
    if (!next.length) return toast("Keep at least one target class");
    state.target = next;
    changed(true);
    b.classList.toggle("on");
  }));
  el.querySelectorAll(".choice").forEach((b) => (b.onclick = () => {
    wiz.answers = [...wiz.answers.slice(0, wiz.at), +b.dataset.c];
    wiz.at++;
    renderWizard();
  }));
  el.querySelectorAll("[data-act=save]").forEach((b) => (b.onclick = async () => {
    const name = $("#wiz-name").value.trim();
    if (!name) return $("#wiz-name").focus(), toast("Give the filter a name");
    try {
      if (!(await savePipeline(name, wizPipeline(), $("#wiz-note").value))) return;
    } catch (e) { return toast("Save failed: " + e.message, 5000); }
    if ("load" in b.dataset) loadSavedFilter(SAVED.find((s) => s.name === name));
    closeWizard();
  }));

  // live tree of the answers so far, and what each choice would throw away after them
  const sofar = wizPipeline(Math.max(0, Math.min(wiz.at, n)));
  api("/api/rejection/evaluate", evalReq(sofar)).then((base) => {
    if (token !== wizToken) return;
    renderTree(el.querySelector(".wiz-tree"), base, "Your filter so far");
    if (wiz.at < 0 || wiz.at >= n) return;
    const kept = new Set(base.objects.filter((o) => o.kept).map((o) => o.object_id));
    questionFor(wiz.questions[wiz.at]).choices.forEach((c, i) => {
      if (!c.params) return;
      api("/api/rejection/evaluate", evalReq([...sofar, { rule: wiz.questions[wiz.at], ...c.params }])).then((r) => {
        if (token !== wizToken) return;
        const lost = r.objects.filter((o) => !o.kept && kept.has(o.object_id));
        const t = lost.filter((o) => o.target).length;
        const span = el.querySelector(`.impact[data-c="${i}"]`);
        span.innerHTML = `drops <b>${lost.length}</b>: <span class="${t ? "lose" : ""}">${t} target</span> · ${lost.length - t} other`;
        span.title = classSummary(lost) || "nothing";
      }).catch((e) => { if (token === wizToken) el.querySelector(`.impact[data-c="${i}"]`).textContent = e.message; });
    });
  }).catch((e) => { if (token === wizToken) el.querySelector(".wiz-tree").innerHTML = `<div class="err">${esc(e.message)}</div>`; });
}

// ---------- Rubin detection details (backfill for objects cached before they were kept) ----------

let detailsPoll = null;

async function refreshDetails(start = false) {
  const el = $("#details-note");
  let st;
  try { st = await api("/api/rejection/details", start ? {} : undefined); } catch (e) { return; }
  clearTimeout(detailsPoll);
  if (!st.running && !st.missing) {
    if (!el.hidden && st.total) { toast(`Fetched ANTARES data for ${st.done} objects`); changed(); }
    el.hidden = true;
    return;
  }
  el.hidden = false;
  el.innerHTML = st.running
    ? `<span class="hint">Fetching detection details and catalog matches from ANTARES: ${st.done} of ${st.total}${st.errors ? ` (${st.errors} failed)` : ""}…</span>`
    : `<span class="hint"><b>${st.missing}</b> cached objects are missing Rubin detection details (extendedness, dipoles, PSF fit, size, glints, measurement flags) or ANTARES's catalog matches (Gaia stars, Milliquas), so those rules see them as no data.</span>
       <button class="small primary" id="details-fetch">Fetch them</button>`;
  $("#details-fetch")?.addEventListener("click", () => refreshDetails(true));
  if (st.running) detailsPoll = setTimeout(refreshDetails, 3000);
}

// ---------- setup ----------

async function init() {
  CFG = await api("/api/rejection/config");
  state = loadState();
  try { SAVED = await api("/api/rejection/saved"); } catch (e) { toast("Couldn't load saved filters: " + e.message, 5000); }
  renderSaved();
  $("#wiz-start").onclick = startWizard;
  refreshDetails();
  renderSettings();
  renderPipeline();
  renderSweepParams();
  $("#include-alt").onchange = (e) => { state.include_alt = e.target.checked; changed(); };
  $("#all-sources").onchange = (e) => { state.all_sources = e.target.checked; changed(); };
  document.querySelectorAll("#truth button").forEach((b) => (b.onclick = () => { state.truth = b.dataset.v; changed(true); }));
  $("#sweep-param").onchange = setSweepRange;
  $("#sweep-run").onclick = runSweep;
  await evaluatePipeline();
  if (sweepOptions().length) runSweep();
}

init().catch((e) => ($("#err").textContent = e.message));
