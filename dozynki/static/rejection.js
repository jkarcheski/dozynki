// DOZYNKI filter lab: compare two rejection pipelines against the labels. Plain JS, no build step.

const STORE_KEY = "dozynki.filterlab.v1";
const METRICS = [
  { key: "completeness", label: "Completeness", hint: "target kept / target", higher: true },
  { key: "purity", label: "Purity", hint: "target kept / kept", higher: true },
  { key: "fpr", label: "False-positive rate", hint: "non-target kept / non-target", higher: false },
  { key: "bogus_leak", label: "Bogus leak", hint: "bogus kept / bogus", higher: false },
];
// default sweep range per parameter name
const SWEEP_RANGE = { max_sep: [0, 5], n: [1, 10], max_frac: [0, 1], max_rms: [0, 2], min_rel: [0, 1], max_p: [0, 1], days: [0, 30], min_b: [0, 30] };
const INTEGER_PARAMS = new Set(["n"]);

let CFG = null;
let state = null;
let results = { A: null, B: null };
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
  const A = pipelineFrom(CFG.default_pipeline);
  // B starts as A without the multi-night cut, the biggest single trade-off in the default pipeline
  const B = A.map((s) => ({ ...s, params: { ...s.params }, on: s.rule === "min_nights" ? false : s.on }));
  return { target: ["transient"], include_alt: false, truth: "blind", all_sources: false, A, B, names: { A: null, B: null } };
}

function loadState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY));
    if (saved && Array.isArray(saved.A) && Array.isArray(saved.B)) {
      // drop rules that no longer exist, append new ones (off), and fill in new params
      const byName = Object.fromEntries(CFG.rules.map((r) => [r.name, r]));
      for (const k of ["A", "B"]) {
        const kept = saved[k].filter((s) => byName[s.rule])
          .map((s) => ({ rule: s.rule, on: !!s.on, params: { ...byName[s.rule].defaults, ...s.params } }));
        const missing = CFG.rules.filter((r) => !kept.some((s) => s.rule === r.name))
          .map((r) => ({ rule: r.name, on: false, params: { ...r.defaults } }));
        saved[k] = [...kept, ...missing];
      }
      return { ...defaultState(), ...saved, names: { A: null, B: null, ...saved.names } };
    }
  } catch (e) { /* storage blocked or corrupt: start fresh */ }
  return defaultState();
}

function saveState() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch (e) { /* not essential */ }
}

const requestFor = (which) => ({
  pipeline: state[which].filter((s) => s.on).map((s) => ({ rule: s.rule, ...s.params })),
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

function renderPipeline(which) {
  const other = which === "A" ? "B" : "A";
  const el = $(`#pipe-${which}`);
  const rules = Object.fromEntries(CFG.rules.map((r) => [r.name, r]));
  const name = state.names[which];
  const saved = SAVED.find((s) => s.name === name);
  const edited = saved && !sameSteps(saved.config, requestFor(which).pipeline);
  el.innerHTML = `
    <div class="card-head">
      <h2 class="pipe-title"><i style="background:var(--series-${which === "A" ? 1 : 2})"></i>Pipeline ${which}
        ${saved ? `<span class="muted">· ${esc(name)}${edited ? " (edited)" : ""}</span>` : ""}</h2>
      <span>
        <select class="small" data-act="load" aria-label="Load a saved filter into ${which}"><option value="">Load saved…</option>
          ${SAVED.map((s, i) => `<option value="${i}">${esc(s.name)}</option>`).join("")}</select>
        <button class="small" data-act="save">Save…</button>
        <button class="small" data-act="copy">Copy to ${other}</button> <button class="small" data-act="reset">Reset</button></span>
    </div>
    ${state[which].map((s, i) => `
      <div class="rule ${s.on ? "" : "off"}">
        <input type="checkbox" data-i="${i}" ${s.on ? "checked" : ""} aria-label="enable ${esc(s.rule)}">
        <span class="name">${esc(s.rule)}${rules[s.rule].broker ? '<span class="tag" title="reads broker output">broker</span>' : ""}</span>
        <span class="params">${Object.entries(s.params).map(([k, v]) =>
          `<label>${esc(k)} <input type="number" step="${INTEGER_PARAMS.has(k) ? 1 : "any"}" data-i="${i}" data-p="${esc(k)}" value="${v}"></label>`).join("")}</span>
        <span class="desc">${esc(rules[s.rule].description)}</span>
      </div>`).join("")}`;
  el.querySelectorAll('input[type=checkbox]').forEach((c) => (c.onchange = () => {
    state[which][+c.dataset.i].on = c.checked;
    renderPipeline(which);
    changed();
  }));
  el.querySelectorAll("input[data-p]").forEach((inp) => (inp.oninput = () => {
    const v = Number(inp.value);
    if (inp.value === "" || !Number.isFinite(v)) return;
    state[which][+inp.dataset.i].params[inp.dataset.p] = v;
    refreshTitle(which);
    changed();
  }));
  el.querySelector('[data-act=load]').onchange = (e) => e.target.value && loadInto(which, SAVED[+e.target.value]);
  el.querySelector('[data-act=save]').onclick = async () => {
    const name = prompt(`Save pipeline ${which} as:`, state.names[which] || "");
    if (!name?.trim()) return;
    try {
      if (!(await savePipeline(name.trim(), requestFor(which).pipeline, saved?.name === name.trim() ? saved.note : null))) return;
    } catch (e) { return toast("Save failed: " + e.message, 5000); }
    state.names[which] = name.trim();
    saveState();
    renderPipeline(which);
  };
  el.querySelector('[data-act=copy]').onclick = () => {
    state[other] = JSON.parse(JSON.stringify(state[which]));
    state.names[other] = state.names[which];
    renderPipeline(other);
    changed();
  };
  el.querySelector('[data-act=reset]').onclick = () => {
    state[which] = pipelineFrom(CFG.default_pipeline);
    state.names[which] = null;
    renderPipeline(which);
    changed();
  };
}

// Update only the title's "(edited)" mark, so typing in a threshold box keeps its focus.
function refreshTitle(which) {
  const saved = SAVED.find((s) => s.name === state.names[which]);
  const tag = $(`#pipe-${which} .pipe-title .muted`);
  if (saved && tag) tag.textContent = `· ${saved.name}${sameSteps(saved.config, requestFor(which).pipeline) ? "" : " (edited)"}`;
}

// ---------- evaluation ----------

let debounce = null;
function changed(settings = false) {
  saveState();
  if (settings) renderSettings();
  renderSweepParams();
  clearTimeout(debounce);
  debounce = setTimeout(evaluateBoth, 200);
}

async function evaluateBoth() {
  const token = ++evalToken;
  try {
    const [A, B] = await Promise.all([api("/api/rejection/evaluate", requestFor("A")), api("/api/rejection/evaluate", requestFor("B"))]);
    if (token !== evalToken) return; // a newer request superseded this one
    results = { A, B };
    $("#err").textContent = "";
    renderAll();
  } catch (e) {
    if (token === evalToken) $("#err").textContent = e.message;
  }
}

function renderAll() {
  const { A } = results;
  const p = A.population;
  const excl = Object.entries(p.excluded).map(([k, n]) => `${n} ${k}`).join(", ");
  $("#pop").innerHTML = `<b>${p.scored}</b> scored objects, <b>${p.n_target}</b> target` + (excl ? ` · excluded ${esc(excl)}` : "") +
    (p.n_target < 30 ? ` · <span class="err">few targets: wide intervals</span>` : "");
  renderMetrics();
  renderTrees();
  renderByClass();
  renderSteps();
  renderDiff();
  drawSweepMarker();
}

function cell(m) {
  if (!m || m.value === null) return `<td class="num">— <span class="ci">(${m ? `${m.k}/${m.n}` : ""})</span></td>`;
  return `<td class="num">${pct(m.value)} <span class="ci">[${pct(m.ci95[0])}, ${pct(m.ci95[1])}] (${m.k}/${m.n})</span></td>`;
}

function renderMetrics() {
  const { A, B } = results;
  const rows = METRICS.map((mt) => {
    const a = A.pipeline_metrics[mt.key], b = B.pipeline_metrics[mt.key];
    const d = a.value !== null && b.value !== null ? b.value - a.value : null;
    const cls = d === null || Math.abs(d) < 1e-9 ? "" : (d > 0) === mt.higher ? "better" : "worse";
    return `<tr><th title="${esc(mt.hint)}">${mt.label} <span class="ci">${esc(mt.hint)}</span></th>${cell(A.baseline[mt.key])}${cell(a)}${cell(b)}
      <td class="num ${cls}">${d === null ? "—" : (d >= 0 ? "+" : "") + d.toFixed(3)}</td></tr>`;
  });
  rows.push(`<tr><th>Kept</th><td class="num">${A.baseline.kept}</td><td class="num">${A.pipeline_metrics.kept}</td>
    <td class="num">${B.pipeline_metrics.kept}</td><td class="num">${B.pipeline_metrics.kept - A.pipeline_metrics.kept >= 0 ? "+" : ""}${B.pipeline_metrics.kept - A.pipeline_metrics.kept}</td></tr>`);
  $("#metrics").innerHTML = `<tr><th></th><th class="num">No filter</th><th class="num">A</th><th class="num">B</th><th class="num">B − A</th></tr>${rows.join("")}`;
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
  const a = classCounts(results.A), b = classCounts(results.B);
  const classes = CFG.classes.map((c) => c.key).filter((k) => a[k]);
  const label = (k) => k + (a[k].target ? (a[k].target === a[k].total ? " ★" : " ☆") : "");
  const x = classes.map(label);
  const bar = (name, ys, color) => ({
    type: "bar", name, x, y: ys, marker: { color, line: { color: cssVar("--card"), width: 2 } },
    hovertemplate: `${name}: %{y} kept<extra>%{x}</extra>`,
  });
  Plotly.react("by-class", [
    bar("No filter", classes.map((k) => a[k].total), cssVar("--baseline")),
    bar("A", classes.map((k) => a[k].kept), cssVar("--series-1")),
    bar("B", classes.map((k) => b[k]?.kept ?? 0), cssVar("--series-2")),
  ], plotLayout({ barmode: "group", bargap: 0.3, bargroupgap: 0.08, yaxis: { title: "objects", gridcolor: cssVar("--line"), rangemode: "tozero" } }),
  { displayModeBar: false, responsive: true });
  $("#by-class-table").innerHTML = `<tr><th>class</th><th class="num">labeled</th><th class="num">of which target</th><th class="num">kept by A</th><th class="num">kept by B</th></tr>` +
    classes.map((k) => `<tr><td>${esc(k)}</td><td class="num">${a[k].total}</td><td class="num">${a[k].target}</td><td class="num">${a[k].kept}</td><td class="num">${b[k]?.kept ?? 0}</td></tr>`).join("") +
    `<tr><td colspan="5" class="hint">★ all of the class is target, ☆ part of it (a subclass or could-also-be target)</td></tr>`;
}

function renderSteps() {
  const rows = [];
  for (const which of ["A", "B"]) {
    const res = results[which];
    const nT = res.population.n_target, nN = res.population.scored - nT;
    for (const [step, st] of Object.entries(res.steps)) {
      const lostT = nT - st.alone.completeness.k, cutN = nN - st.alone.fpr.k;
      const only = Object.entries(st.unique_rejects).filter(([, n]) => n).map(([c, n]) => `${c} ${n}`).join(", ") || "—";
      rows.push(`<tr><td>${which}</td><td>${esc(step)}${st.broker ? '<span class="tag">broker</span>' : ""}</td>
        <td class="num">${st.no_data}</td><td class="num">${lostT}</td><td class="num">${cutN}</td><td>${esc(only)}</td>
        ${cell(st.without_this_step.completeness)}${cell(st.without_this_step.purity)}</tr>`);
    }
  }
  $("#steps").innerHTML = `<tr><th></th><th>step</th><th class="num">no data</th><th class="num">target lost alone</th><th class="num">non-target cut alone</th>
    <th>only it rejects</th><th class="num">completeness without</th><th class="num">purity without</th></tr>${rows.join("")}`;
}

function outcome(o) {
  return o.kept ? "kept" : `rejected · ${esc(o.rejected_by)}`;
}

function renderDiff() {
  const bById = Object.fromEntries(results.B.objects.map((o) => [o.object_id, o]));
  const diff = results.A.objects.filter((o) => bById[o.object_id] && bById[o.object_id].kept !== o.kept);
  diff.sort((x, y) => (y.target - x.target) || x.truth.localeCompare(y.truth));
  $("#diff").innerHTML = diff.length
    ? `<tr><th>object</th><th>label</th><th>target</th><th>A</th><th>B</th></tr>` + diff.map((o) => {
        const b = bById[o.object_id];
        const lab = o.truth + (o.subclass ? ` · ${o.subclass}` : "") + (o.alt_classes ? ` (or ${o.alt_classes})` : "");
        return `<tr><td><a href="/vet?object=${encodeURIComponent(o.object_id)}" target="_blank">${esc(o.object_id)}</a></td>
          <td>${esc(lab)}</td><td>${o.target ? "✓" : ""}</td><td>${outcome(o)}</td><td>${outcome(b)}</td></tr>`;
      }).join("")
    : `<tr><td class="hint">A and B keep exactly the same objects.</td></tr>`;
}

// ---------- sweep ----------

function sweepOptions() {
  const out = [];
  state.A.forEach((s, i) => {
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
  if (!rule) return toast("Enable a rule with a threshold in pipeline A first");
  const body = requestFor("A");
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
    const s = state.A.find((s) => s.rule === rule);
    if (!s) return;
    s.params[param] = INTEGER_PARAMS.has(param) ? Math.round(v) : +v.toPrecision(4);
    renderPipeline("A");
    changed();
    toast(`A: ${rule} ${param} = ${s.params[param]}`);
  });
}

function markerShape() {
  if (!lastSweep) return [];
  const s = state.A.find((s) => s.rule === lastSweep.rule && s.on);
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
const QUESTION_ORDER = ["sso_association", "gaia_star", "milliquas_agn", "galactic_plane", "negative_flux", "position_scatter",
  "min_detections", "min_lsst_detections", "min_nights", "min_span", "lsst_reliability", "alerce_stamp_bogus"];

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
  for (const which of ["A", "B"]) {
    const name = state.names?.[which];
    renderTree($(`#tree-${which}`), results[which],
      `<i style="background:var(--series-${which === "A" ? 1 : 2})"></i>Pipeline ${which}${name ? ` · ${esc(name)}` : ""}`);
  }
}

// ---------- saved filters ----------

let SAVED = [];

async function loadSaved() {
  SAVED = await api("/api/rejection/saved");
  renderSaved();
  renderPipeline("A");
  renderPipeline("B");
}

function stepChip(step) {
  const p = stepParams(step);
  return `<span class="pill" title="${esc(nodeText(step))}">${esc(step.rule)}${Object.keys(p).length ? " " + esc(Object.values(p).join(", ")) : ""}</span>`;
}

function renderSaved() {
  $("#saved").innerHTML = SAVED.length
    ? `<tr><th>name</th><th>questions, in order</th><th>note</th><th>saved</th><th></th></tr>` + SAVED.map((s, i) => `<tr>
        <td><b>${esc(s.name)}</b></td>
        <td class="wrap">${s.config.map(stepChip).join("") || '<span class="hint">none: keeps everything</span>'}</td>
        <td class="wrap hint">${esc(s.note || "")}</td>
        <td class="hint">${esc(s.updated_at.slice(0, 16).replace("T", " "))}</td>
        <td><button class="small" data-i="${i}" data-to="A">Load into A</button> <button class="small" data-i="${i}" data-to="B">Load into B</button>
          <button class="small" data-i="${i}" data-del title="Delete">✕</button></td></tr>`).join("")
    : `<tr><td class="hint wrap">Nothing saved yet. Save a pipeline from its card, or build one with questions.</td></tr>`;
  $("#saved").querySelectorAll("button[data-to]").forEach((b) => (b.onclick = () => loadInto(b.dataset.to, SAVED[+b.dataset.i])));
  $("#saved").querySelectorAll("button[data-del]").forEach((b) => (b.onclick = async () => {
    const s = SAVED[+b.dataset.i];
    if (!confirm(`Delete saved filter "${s.name}"?`)) return;
    try {
      await fetch(`/api/rejection/saved/${encodeURIComponent(s.name)}`, { method: "DELETE" });
      for (const k of ["A", "B"]) if (state.names[k] === s.name) state.names[k] = null;
      saveState();
      await loadSaved();
      toast(`Deleted "${s.name}"`);
    } catch (e) { toast("Delete failed: " + e.message, 5000); }
  }));
}

function loadInto(which, saved) {
  state[which] = pipelineFrom(saved.config);
  state.names[which] = saved.name;
  renderPipeline(which);
  changed();
  toast(`Loaded "${saved.name}" into ${which}`);
}

async function savePipeline(name, pipeline, note) {
  if (SAVED.some((s) => s.name === name) && !confirm(`Overwrite saved filter "${name}"?`)) return false;
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

const evalReq = (pipeline) => ({ ...requestFor("A"), pipeline });

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
      <p><button class="primary" data-act="save" data-to="A">Save and load into A</button>
        <button data-act="save" data-to="B">Save and load into B</button>
        <button data-act="save">Just save</button></p>`;
  }
  el.innerHTML = `<div class="wiz"><div class="wiz-main">${nav}${body}</div><div class="wiz-tree"><div class="hint">building…</div></div></div>`;

  el.querySelector("[data-act=close]").onclick = closeWizard;
  el.querySelector("[data-act=back]")?.addEventListener("click", () => { wiz.at--; renderWizard(); });
  el.querySelector("[data-act=next]")?.addEventListener("click", () => { wiz.at = 0; renderWizard(); });
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
    if (b.dataset.to) loadInto(b.dataset.to, SAVED.find((s) => s.name === name));
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

// ---------- setup ----------

async function init() {
  CFG = await api("/api/rejection/config");
  state = loadState();
  try { SAVED = await api("/api/rejection/saved"); } catch (e) { toast("Couldn't load saved filters: " + e.message, 5000); }
  renderSaved();
  $("#wiz-start").onclick = startWizard;
  renderSettings();
  renderPipeline("A");
  renderPipeline("B");
  renderSweepParams();
  $("#include-alt").onchange = (e) => { state.include_alt = e.target.checked; changed(); };
  $("#all-sources").onchange = (e) => { state.all_sources = e.target.checked; changed(); };
  document.querySelectorAll("#truth button").forEach((b) => (b.onclick = () => { state.truth = b.dataset.v; changed(true); }));
  $("#sweep-param").onchange = setSweepRange;
  $("#sweep-run").onclick = runSweep;
  await evaluateBoth();
  if (sweepOptions().length) runSweep();
}

init().catch((e) => ($("#err").textContent = e.message));
