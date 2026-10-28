// DOZYNKI label comparison: confusion matrix of two label sources (labelers, ALeRCE classifiers). Plain JS, no build step.

const STORE_KEY = "dozynki.compare.v1";

let CFG = null;
let state = null;
let result = null;
let selected = null; // {row, col} or null for every disagreement
let evalToken = 0;

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const num = (v, d = 3) => (v === null || v === undefined ? "—" : v.toFixed(d));
const srcLabel = (id) => CFG.sources.find((s) => s.id === id)?.label ?? id;

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

function defaultState() {
  const labels = CFG.sources.filter((s) => s.kind === "label");
  const alerce = CFG.sources.filter((s) => s.kind === "alerce").sort((a, b) => b.n - a.n || b.id.localeCompare(a.id));
  const ref = labels[0]?.id ?? CFG.sources[0]?.id;
  const comp = alerce[0]?.id ?? CFG.sources.find((s) => s.id !== ref)?.id;
  return { ref, comp, view: "class", norm: "row", min_prob: 0, include_alt: false, all_sources: false };
}

function loadState() {
  const ids = new Set(CFG.sources.map((s) => s.id));
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY));
    if (saved) {
      const s = { ...defaultState(), ...saved };
      if (ids.has(s.ref) && ids.has(s.comp)) return s;
    }
  } catch (e) { /* storage blocked or corrupt: start fresh */ }
  return defaultState();
}

function saveState() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch (e) { /* not essential */ }
}

const request = () => ({
  ref: state.ref, comp: state.comp, view: state.view, min_prob: state.min_prob,
  include_alt: state.include_alt, all_sources: state.all_sources,
});

// ---------- settings ----------

function renderSettings() {
  const opts = (cur) => CFG.sources.map((s) =>
    `<option value="${esc(s.id)}" ${s.id === cur ? "selected" : ""}>${esc(s.label)} (${s.n})</option>`).join("");
  $("#ref").innerHTML = opts(state.ref);
  $("#comp").innerHTML = opts(state.comp);
  document.querySelectorAll("#view button").forEach((b) => b.classList.toggle("on", b.dataset.v === state.view));
  document.querySelectorAll("#norm button").forEach((b) => b.classList.toggle("on", b.dataset.v === state.norm));
  $("#min-prob").value = state.min_prob;
  $("#include-alt").checked = state.include_alt;
  $("#all-sources").checked = state.all_sources;
}

let debounce = null;
function changed() {
  saveState();
  renderSettings();
  selected = null;
  clearTimeout(debounce);
  debounce = setTimeout(run, 150);
}

async function run() {
  const token = ++evalToken;
  try {
    const [res, summary] = await Promise.all([api("/api/compare", request()), api("/api/compare/summary", request())]);
    if (token !== evalToken) return; // a newer request superseded this one
    result = res;
    $("#err").textContent = "";
    renderAll();
    renderSummary(summary);
  } catch (e) {
    if (token !== evalToken) return;
    result = null;
    $("#err").textContent = e.message;
    for (const id of ["#matrix", "#metrics", "#per-class", "#cell"]) $(id).innerHTML = "";
  }
}

// ---------- rendering ----------

function renderAll() {
  const excl = Object.entries(result.excluded).map(([k, n]) => `${n} ${k}`).join(", ");
  $("#pop").innerHTML = `<b>${result.n}</b> objects classified by both` + (excl ? ` · excluded ${esc(excl)}` : "");
  renderMatrix();
  renderMetrics();
  renderPerClass();
  renderCell();
}

function axisLabel(key, mapsTo) {
  const m = mapsTo[key];
  return `${esc(key)}${m && m !== key ? `<span class="maps">→ ${esc(m)}</span>` : ""}`;
}

function renderMatrix() {
  const { rows, cols, matrix, row_maps_to: rmap, col_maps_to: cmap } = result;
  const rowTot = matrix.map((r) => r.reduce((a, b) => a + b, 0));
  const colTot = cols.map((_, j) => matrix.reduce((a, r) => a + r[j], 0));
  const max = Math.max(1, ...matrix.flat());
  const head = `<tr><th class="axis" colspan="2" style="text-align:right" title="rows: ${esc(srcLabel(result.ref))} · columns: ${esc(srcLabel(result.comp))}">compared →<br>↓ reference</th>
    ${cols.map((c) => `<th>${axisLabel(c, cmap)}</th>`).join("")}<th class="axis">total</th></tr>`;
  const body = rows.map((r, i) => `<tr><th colspan="2" style="text-align:right">${axisLabel(r, rmap)}</th>` + cols.map((c, j) => {
    const n = matrix[i][j];
    const frac = state.norm === "row" ? n / (rowTot[i] || 1) : state.norm === "col" ? n / (colTot[j] || 1) : n / max;
    const pct = Math.round(frac * 85);
    const agree = rmap[r] === cmap[c];
    const sel = selected && selected.row === r && selected.col === c;
    const sub = state.norm === "count" ? "" : `<span class="sub">${n ? (100 * frac).toFixed(0) + "%" : ""}</span>`;
    const tip = `${r} → ${c}: ${n} objects · ${(100 * n / (rowTot[i] || 1)).toFixed(1)}% of row, ${(100 * n / (colTot[j] || 1)).toFixed(1)}% of column`;
    return `<td class="c ${agree ? "agree" : ""} ${sel ? "sel" : ""} ${pct > 50 ? "dark" : ""}" data-r="${esc(r)}" data-c="${esc(c)}" title="${esc(tip)}"
      style="background:color-mix(in srgb, var(--series-1) ${pct}%, var(--card));${pct > 50 ? "color:#fff" : ""}">${n || ""}${sub}</td>`;
  }).join("") + `<td class="tot">${rowTot[i]}</td></tr>`).join("");
  const foot = `<tr><th colspan="2" class="axis" style="text-align:right">total</th>${colTot.map((n) => `<td class="tot">${n}</td>`).join("")}<td class="tot">${result.n}</td></tr>`;
  $("#matrix").innerHTML = head + body + foot;
  $("#matrix").querySelectorAll("td.c").forEach((td) => (td.onclick = () => {
    selected = { row: td.dataset.r, col: td.dataset.c };
    renderMatrix();
    renderCell();
  }));
}

function metricCell(m) {
  if (!m || m.value === null) return `<td class="num">— <span class="ci">(${m ? `${m.k}/${m.n}` : ""})</span></td>`;
  return `<td class="num">${num(m.value)} <span class="ci">[${num(m.ci95[0])}, ${num(m.ci95[1])}] (${m.k}/${m.n})</span></td>`;
}

function renderMetrics() {
  const r = result;
  $("#metrics").innerHTML = `
    <tr><th title="objects whose classes map to the same thing">Accuracy (agreement)</th>${metricCell(r.accuracy)}</tr>
    ${state.include_alt ? `<tr><th title="also agree when a could-also-be class matches">… incl. could-also-be</th>${metricCell(r.accuracy_alt)}</tr>` : ""}
    <tr><th title="agreement beyond what the two class distributions give by chance; 0 = chance, 1 = perfect">Cohen's κ</th><td class="num">${num(r.kappa)}</td></tr>`;
}

function renderPerClass() {
  const entries = Object.entries(result.per_class);
  $("#per-class").innerHTML = `<tr><th>class</th><th class="num" title="reference objects in this class">ref n</th><th class="num" title="compared objects in this class">comp n</th>
    <th class="num" title="of the reference's objects in this class, the share the compared source also puts there">recall</th>
    <th class="num" title="of the compared source's objects in this class, the share the reference agrees with">precision</th><th class="num">F1</th></tr>` +
    entries.map(([k, c]) => `<tr><td>${esc(k)}</td><td class="num">${c.n_ref}</td><td class="num">${c.n_comp}</td>
      ${metricCell(c.recall)}${metricCell(c.precision)}<td class="num">${num(c.f1)}</td></tr>`).join("");
}

function renderSummary(all) {
  const rows = all.filter((s) => s.n);
  const none = all.length - rows.length;
  $("#summary").innerHTML = `<tr><th>source</th><th class="num">n shared</th><th class="num">accuracy</th>
    ${state.include_alt ? `<th class="num">incl. could-also-be</th>` : ""}<th class="num">κ</th></tr>` +
    rows.map((s) => `<tr class="clickable ${s.id === state.comp ? "chosen" : ""}" data-id="${esc(s.id)}"><td>${esc(s.label)}</td><td class="num">${s.n}</td>
      ${s.n ? metricCell(s.accuracy) : "<td class='num'>—</td>"}${state.include_alt ? (s.n ? metricCell(s.accuracy_alt) : "<td></td>") : ""}
      <td class="num">${num(s.kappa)}</td></tr>`).join("") +
    (none ? `<tr><td colspan="5" class="hint">${none} other source${none > 1 ? "s" : ""} share no comparable objects with the reference.</td></tr>` : "");
  $("#summary").querySelectorAll("tr[data-id]").forEach((tr) => (tr.onclick = () => { state.comp = tr.dataset.id; changed(); }));
}

const topProbs = (probs) => probs
  ? Object.entries(probs).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k, p]) => `${esc(k)} ${p.toFixed(2)}`).join(", ")
  : "";

function renderCell() {
  const objs = result.objects.filter((o) => selected ? o.row === selected.row && o.col === selected.col : !o.agree);
  // most confident broker calls first: those are the disagreements most worth a second look
  objs.sort((a, b) => (b.comp_p ?? b.ref_p ?? 0) - (a.comp_p ?? a.ref_p ?? 0) || a.object_id.localeCompare(b.object_id));
  $("#cell-title").textContent = selected
    ? `${selected.row} → ${selected.col} (${objs.length})`
    : `All disagreements (${objs.length})`;
  const hasProbs = result.objects.some((o) => o.comp_probs || o.ref_probs);
  const lab = (cls, sub, alt) => esc(cls) + (sub ? ` · ${esc(sub)}` : "") + (alt?.length ? ` <span class="muted">(or ${esc(alt.join(", "))})</span>` : "");
  $("#cell").innerHTML = objs.length
    ? `<tr><th>object</th><th>reference</th><th>compared</th>${hasProbs ? "<th>top probabilities</th>" : ""}</tr>` + objs.map((o) =>
        `<tr><td><a href="/vet?object=${encodeURIComponent(o.object_id)}" target="_blank">${esc(o.object_id)}</a></td>
          <td>${lab(o.row, o.ref_subclass, o.ref_alt)}</td><td>${lab(o.col, null, o.comp_alt)}</td>
          ${hasProbs ? `<td>${topProbs(o.comp_probs || o.ref_probs)}</td>` : ""}</tr>`).join("")
    : `<tr><td class="hint">${selected ? "No objects in this cell." : "The two sources agree on every object."}</td></tr>`;
}

// ---------- setup ----------

async function init() {
  CFG = await api("/api/compare/config");
  if (CFG.sources.length < 2) {
    $("#err").textContent = "Need at least two label sources: label some objects first.";
    return;
  }
  state = loadState();
  renderSettings();
  $("#ref").onchange = (e) => { state.ref = e.target.value; changed(); };
  $("#comp").onchange = (e) => { state.comp = e.target.value; changed(); };
  $("#swap").onclick = () => { [state.ref, state.comp] = [state.comp, state.ref]; changed(); };
  document.querySelectorAll("#view button").forEach((b) => (b.onclick = () => { state.view = b.dataset.v; changed(); }));
  document.querySelectorAll("#norm button").forEach((b) => (b.onclick = () => {
    state.norm = b.dataset.v;
    saveState();
    renderSettings();
    if (result) renderMatrix();
  }));
  $("#min-prob").oninput = (e) => {
    const v = Number(e.target.value);
    if (e.target.value === "" || !Number.isFinite(v)) return;
    state.min_prob = v;
    saveState();
    clearTimeout(debounce);
    debounce = setTimeout(run, 250);
  };
  $("#include-alt").onchange = (e) => { state.include_alt = e.target.checked; changed(); };
  $("#all-sources").onchange = (e) => { state.all_sources = e.target.checked; changed(); };
  $("#show-all-dis").onclick = () => { selected = null; if (result) { renderMatrix(); renderCell(); } };
  await run();
}

init().catch((e) => ($("#err").textContent = e.message));
