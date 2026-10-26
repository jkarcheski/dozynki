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
  return { target: ["transient"], include_alt: false, truth: "blind", all_sources: false, A, B };
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
      return { ...defaultState(), ...saved };
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

function renderSettings() {
  const targets = [
    ...CFG.classes.filter((c) => c.real !== null).map((c) => ({ key: c.key, label: c.label })),
    ...CFG.transient_subclasses.map((s) => ({ key: `transient:${s.key}`, label: `transient · ${s.label}` })),
  ];
  $("#targets").innerHTML = targets
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
  el.innerHTML = `
    <div class="card-head">
      <h2 class="pipe-title"><i style="background:var(--series-${which === "A" ? 1 : 2})"></i>Pipeline ${which}</h2>
      <span><button class="small" data-act="copy">Copy to ${other}</button> <button class="small" data-act="reset">Reset</button></span>
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
    changed();
  }));
  el.querySelector('[data-act=copy]').onclick = () => {
    state[other] = JSON.parse(JSON.stringify(state[which]));
    renderPipeline(other);
    changed();
  };
  el.querySelector('[data-act=reset]').onclick = () => {
    state[which] = pipelineFrom(CFG.default_pipeline);
    renderPipeline(which);
    changed();
  };
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

// ---------- setup ----------

async function init() {
  CFG = await api("/api/rejection/config");
  state = loadState();
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
