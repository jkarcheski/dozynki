// DOZYNKI labeling UI. Plain JS, no build step.

const BAND_COLORS = { u: "#7b3fc4", g: "#2a9d4b", r: "#d1352b", i: "#e08a1e", z: "#8a5a2b", y: "#555" };
const LS_SIZE = 256;            // cutout size in pixels (pixscale follows the zoom level)
const FIELD_PX = 320;           // displayed size
const FOV_LEVELS = [10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 360]; // field width, arcsec
const DEFAULT_FOV = 30;

let CFG = null;
let cur = null;                 // current /api/object payload
let lcMode = "sci";
let fieldFov = DEFAULT_FOV;
let selectedPoint = null;
let form = blankForm();
const skipped = [];

function blankForm() {
  return { klass: null, alts: new Set(), subclass: null, flags: new Set(), confidence: "medium", host: null, notes: "" };
}

const $ = (s) => document.querySelector(s);
const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "—" : typeof v === "number" ? v.toFixed(d) : v);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function toast(msg, ms = 2500) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), ms);
}

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
  return r.json();
}

// ---------- setup ----------

async function init() {
  CFG = await api("/api/config");
  $("#blind").checked = CFG.blind;
  $("#blind").onchange = async (e) => {
    await api("/api/config/blind", { method: "POST", body: { blind: e.target.checked } });
    CFG.blind = e.target.checked;
    if (cur) loadObject(cur.evidence.object_id);
  };
  buildLabelBar();
  initFilters();
  $("#fill").onclick = fillQueue;
  $("#add-ids").onclick = addIds;
  $("#watch-recheck").onclick = recheckWatchlist;
  $("#export").onclick = doExport;
  $("#commit").onclick = commit;
  $("#skip").onclick = skip;
  $("#next").onclick = next;
  $("#hostless").onclick = () => setHost({ hostless: true });
  $("#skybot").onclick = runSkybot;
  $("#xm-radius").value = CFG.xmatch_radius;
  $("#xm-search").onclick = searchXmatch;
  $("#xm-radius").onkeydown = (e) => { if (e.key === "Enter") { e.stopPropagation(); searchXmatch(); } };
  $("#zoom-in").onclick = () => zoom(-1);
  $("#zoom-out").onclick = () => zoom(1);
  $("#field").addEventListener("wheel", (e) => { e.preventDefault(); zoom(e.deltaY > 0 ? 1 : -1); }, { passive: false });
  $("#aladin-survey").onchange = (e) => aladin && setAladinSurvey(e.target.value);
  $("#notes").oninput = (e) => (form.notes = e.target.value);
  document.querySelectorAll("#lc-mode button").forEach((b) => (b.onclick = () => setLcMode(b.dataset.mode)));
  document.addEventListener("keydown", onKey);
  refreshStats();
  // /?object=<diaObjectId> opens that object (links from the filter lab)
  const linked = new URLSearchParams(location.search).get("object");
  linked ? loadObject(linked) : next();
}

function buildLabelBar() {
  $("#classes").innerHTML = CFG.classes
    .map((c) => `<button data-k="${c.key}">${esc(c.label)}<kbd>${c.hotkey}</kbd></button>`)
    .join("");
  $("#classes").querySelectorAll("button").forEach((b) => (b.onclick = (e) => (e.shiftKey ? toggleAlt(b.dataset.k) : setClass(b.dataset.k))));
  $("#classes").insertAdjacentHTML("beforeend", `<span class="muted" style="font-size:12px">click = label · shift+click / shift+key = could also be</span>`);
  $("#subclasses").innerHTML =
    `<span class="muted" style="font-size:12px">Transient type (optional):</span>` +
    CFG.transient_subclasses.map((s) => `<button data-k="${s.key}">${esc(s.label)}</button>`).join("");
  $("#subclasses").querySelectorAll("button").forEach(
    (b) => (b.onclick = () => { form.subclass = form.subclass === b.dataset.k ? null : b.dataset.k; renderForm(); })
  );
  $("#flags").innerHTML = CFG.flags
    .map((f) => `<button data-k="${f.key}">${esc(f.label)}${f.hotkey ? `<kbd>${f.hotkey}</kbd>` : ""}</button>`)
    .join("");
  $("#flags").querySelectorAll("button").forEach((b) => (b.onclick = () => toggleFlag(b.dataset.k)));
  $("#confidence").innerHTML = CFG.confidence.map((c) => `<button data-k="${c}">${c}</button>`).join("");
  $("#confidence").querySelectorAll("button").forEach((b) => (b.onclick = () => { form.confidence = b.dataset.k; renderForm(); }));
}

function toggleFlag(k) {
  form.flags.has(k) ? form.flags.delete(k) : form.flags.add(k);
  renderForm();
}

const transientPicked = () => form.klass === "transient" || form.alts.has("transient");

function setClass(k) {
  form.klass = k;
  form.alts.delete(k);
  if (!transientPicked()) form.subclass = null;
  renderForm();
}

function toggleAlt(k) {
  if (k === form.klass) return;
  form.alts.has(k) ? form.alts.delete(k) : form.alts.add(k);
  if (!transientPicked()) form.subclass = null;
  renderForm();
}

function setHost(h) {
  form.host = h;
  renderForm();
  if (cur) { renderField(cur.evidence); renderXmatch(cur.evidence); updateAladin(cur.evidence); }
}

function renderForm() {
  $("#classes").querySelectorAll("button").forEach((b) => {
    b.classList.toggle("on", b.dataset.k === form.klass);
    b.classList.toggle("alt", form.alts.has(b.dataset.k));
  });
  $("#subclasses").style.display = transientPicked() ? "" : "none";
  $("#subclasses").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.k === form.subclass));
  $("#flags").querySelectorAll("button").forEach((b) => b.classList.toggle("on", form.flags.has(b.dataset.k)));
  $("#confidence").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.k === form.confidence));
  $("#notes").value = form.notes;
  const h = form.host;
  $("#host-display").textContent = !h ? "host: not set" : h.hostless ? "host: hostless" : `host: LS ${h.ls_id} (${fmt(h.sep, 1)}″, z≈${fmt(h.z_phot_median)})`;
  $("#commit").disabled = !cur || !form.klass;
}

function onKey(e) {
  if (e.target.tagName === "SELECT" || e.target.tagName === "TEXTAREA") return;
  if (e.target.tagName === "INPUT" && e.target.type !== "checkbox") {
    if (e.key === "Enter" && e.target.id === "notes") commit();
    if (e.key === "Enter" && e.target.id === "xm-radius") e.preventDefault();
    return;
  }
  if (e.shiftKey && e.code.startsWith("Digit")) {
    const alt = CFG.classes.find((c) => c.hotkey === e.code.slice(5));
    if (alt) return toggleAlt(alt.key);
  }
  const c = CFG.classes.find((c) => c.hotkey === e.key);
  if (c) return setClass(c.key);
  const f = CFG.flags.find((f) => f.hotkey === e.key);
  if (f) return toggleFlag(f.key);
  if (e.key === "Enter") return commit();
  if (e.key === "s") return skip();
  if (e.key === "n") return next();
  if (e.key === "h") return setHost({ hostless: true });
  if (e.key === "f") return setLcMode({ sci: "flux", flux: "mag", mag: "sci" }[lcMode]);
  if (e.key === "+" || e.key === "=") return zoom(-1);
  if (e.key === "-") return zoom(1);
}

// ---------- queue ----------

async function refreshStats() {
  const s = await api("/api/stats");
  const q = s.queue;
  const classes = Object.entries(s.by_class).map(([k, n]) => `${k} <b>${n}</b>`).join(" · ");
  $("#stats").innerHTML =
    `pending <b>${q.pending || 0}</b> · labeled <b>${q.labeled || 0}</b> · watching <b>${q.watch || 0}</b> · skipped <b>${q.skipped || 0}</b>` +
    (classes ? ` &nbsp;|&nbsp; ${classes}` : "") +
    (s.revised_after_reveal ? ` &nbsp;|&nbsp; label changed on a later look <b>${s.revised_after_reveal}</b>` : "");
}

// ---------- filters (shared by sampling and by which queued object comes next) ----------

const FILTER_KEY = "dozynki.filters";
const FILTER_FIELDS = { min_det: "#f-min-det", date_mode: "#f-date-mode", date_from: "#f-date-from", date_to: "#f-date-to" };

function getFilters() {
  return {
    min_det: Math.max(1, +$("#f-min-det").value || 1),
    date_mode: $("#f-date-mode").value,
    date_from: $("#f-date-from").value,
    date_to: $("#f-date-to").value,
  };
}

const filterQuery = () => new URLSearchParams(getFilters()).toString();
const filtersActive = (f = getFilters()) => f.min_det > 1 || f.date_from || f.date_to;

function initFilters() {
  try {
    const saved = JSON.parse(localStorage.getItem(FILTER_KEY) || "{}");
    for (const [k, sel] of Object.entries(FILTER_FIELDS)) if (saved[k] !== undefined) $(sel).value = saved[k];
  } catch {}
  for (const sel of Object.values(FILTER_FIELDS)) $(sel).onchange = onFiltersChanged;
  $("#f-clear").onclick = () => {
    $("#f-min-det").value = 1; $("#f-date-from").value = ""; $("#f-date-to").value = "";
    onFiltersChanged();
  };
  refreshMatching();
}

function onFiltersChanged() {
  try { localStorage.setItem(FILTER_KEY, JSON.stringify(getFilters())); } catch {}
  refreshMatching();
}

async function refreshMatching() {
  $(".filterbar").classList.toggle("on", !!filtersActive());
  if (!filtersActive()) { $("#f-matching").textContent = ""; return; }
  $("#f-matching").textContent = "counting…";
  try {
    const r = await api("/api/queue/matching?" + filterQuery());
    $("#f-matching").textContent = `${r.n_matching} of ${r.n_pending} pending match`;
  } catch (e) {
    $("#f-matching").textContent = e.message;
  }
}

async function fillQueue() {
  const n = +$("#fill-n").value;
  $("#fill").disabled = true;
  try {
    const r = await api("/api/queue/fill", { method: "POST", body: { source: "antares_random_lsst", n, ...getFilters() } });
    toast(`Sampled ${r.returned} objects (seed ${r.seed}), ${r.added} new` + (r.returned === 0 && filtersActive() ? " — nothing in ANTARES matches these filters" : ""), 5000);
    refreshMatching();
    refreshStats();
    if (!cur) next();
  } catch (e) {
    toast("Fill failed: " + e.message, 5000);
  } finally {
    $("#fill").disabled = false;
  }
}

async function addIds() {
  const s = prompt("LSST diaObjectIds (comma or space separated):");
  if (!s) return;
  const ids = s.split(/[\s,]+/).filter(Boolean);
  const r = await api("/api/queue/add", { method: "POST", body: { object_ids: ids } });
  toast(`Added ${r.added}` + (r.not_in_antares.length ? `; not found in ANTARES: ${r.not_in_antares.join(", ")}` : ""), 5000);
  refreshStats();
}

async function recheckWatchlist() {
  $("#watch-recheck").disabled = true;
  toast("Rechecking watched objects…", 10000);
  try {
    const r = await api("/api/watch/recheck", { method: "POST" });
    const errs = Object.keys(r.errors).length;
    toast(`${r.requeued.length} watched object(s) have new alerts and are back in the queue; ${r.unchanged} unchanged` + (errs ? `; ${errs} failed` : ""), 6000);
    refreshStats();
    if (!cur && r.requeued.length) next();
  } catch (e) {
    toast("Recheck failed: " + e.message, 5000);
  } finally {
    $("#watch-recheck").disabled = false;
  }
}

async function doExport() {
  try {
    const r = await api("/api/export", { method: "POST" });
    toast(r.error ? r.error : `Exported ${r.n_objects} objects / ${r.n_labels} labels to ${r.dir}`, 6000);
  } catch (e) {
    toast("Export failed: " + e.message, 5000);
  }
}

async function next() {
  const exclude = cur && !cur.labels.length ? [...skipped, cur.evidence.object_id] : skipped;
  const r = await api(`/api/next?exclude=${encodeURIComponent(exclude.join(","))}&${filterQuery()}`);
  if (filtersActive()) $("#f-matching").textContent = `${r.n_matching} of ${r.n_pending} pending match`;
  if (!r.object_id) {
    cur = null;
    $("#obj-title").textContent = "Queue empty";
    $("#lc").innerHTML = r.n_pending && filtersActive()
      ? `<div class="loading">No pending objects match the filters (${r.n_pending} pending don't). Sample with these filters, or loosen them.</div>`
      : `<div class="loading">Queue is empty — use "Sample random LSST" above.</div>`;
    renderForm();
    return;
  }
  loadObject(r.object_id);
}

async function skip() {
  if (!cur) return;
  await api(`/api/skip/${cur.evidence.object_id}`, { method: "POST" });
  skipped.push(cur.evidence.object_id);
  cur = null;
  refreshStats();
  next();
}

// ---------- object ----------

let loadToken = 0;

async function loadObject(oid) {
  window.scrollTo({ top: 0 }); // each new target starts at the cutouts
  $("#obj-title").textContent = `LSST ${oid}`;
  $("#lc").innerHTML = `<div class="loading">Fetching ANTARES, ALeRCE, Babamul and crossmatches…</div>`;
  ["#cutouts", "#point-detail", "#xmatch", "#opinions", "#obj-links", "#field", "#field-legend", "#astro", "#astro-summary"].forEach((s) => ($(s).innerHTML = ""));
  form = blankForm();
  selectedPoint = null;
  fieldFov = DEFAULT_FOV;
  $("#xm-radius").value = CFG.xmatch_radius;
  renderForm();
  const token = ++loadToken;
  cur = null; // so Commit stays disabled (and can't hit the previous object) until this one arrives
  renderForm();
  let data;
  try {
    data = await api(`/api/object/${oid}`);
  } catch (e) {
    if (token === loadToken) $("#lc").innerHTML = `<div class="loading err">Failed to load: ${esc(e.message)}</div>`;
    return;
  }
  if (token !== loadToken) return; // a newer loadObject started while this one was fetching
  cur = data;
  const ev = cur.evidence;
  const last = cur.labels[cur.labels.length - 1];
  if (last) {
    form.klass = last.klass;
    form.alts = new Set(JSON.parse(last.alt_classes || "[]"));
    form.subclass = last.subclass;
    form.flags = new Set(JSON.parse(last.flags || "[]"));
    form.confidence = last.confidence || "medium";
    form.host = last.host ? JSON.parse(last.host) : null;
    form.notes = last.notes || "";
  }
  $("#obj-links").innerHTML = [
    `<a target="_blank" href="https://antares.noirlab.edu/loci/${ev.locus_id}">ANTARES ${ev.locus_id}</a>`,
    `<a target="_blank" href="https://lsst.alerce.online/object/${ev.object_id}">ALeRCE</a>`,
    ev.ztf_object_id ? `<a target="_blank" href="https://alerce.online/object/${ev.ztf_object_id}">ZTF ${ev.ztf_object_id}</a>` : "",
    `<a target="_blank" href="https://www.legacysurvey.org/viewer?ra=${ev.ra}&dec=${ev.dec}&layer=ls-dr10&zoom=16&mark=${ev.ra},${ev.dec}">LS viewer</a>`,
    `<span class="muted">${fmt(ev.ra, 5)}, ${fmt(ev.dec, 5)} · queued via ${esc(cur.queue.source)}</span>`,
  ].join("");
  if (ev.xmatch?.radius_arcsec) $("#xm-radius").value = ev.xmatch.radius_arcsec;
  renderPhase();
  renderLightcurve(ev);
  renderField(ev);
  renderAstrometry(ev);
  updateAladin(ev);
  renderXmatch(ev);
  renderOpinions();
  renderForm();
  const dets = ev.points.filter((p) => p.kind === "detection").sort((a, b) => b.mjd - a.mjd);
  if (dets.length) selectPoint(dets[0]);
}

function renderPhase() {
  const el = $("#phase");
  const hasBlind = cur.labels.some((l) => l.phase === "blind");
  const last = cur.labels[cur.labels.length - 1];
  if (last && JSON.parse(last.flags || "[]").includes("watch")) {
    const prev = JSON.parse(last.snapshot || "{}");
    el.textContent = `FOLLOW-UP of watched object (last labeled with ${(prev.n_lsst_det ?? 0) + (prev.n_ztf_det ?? 0)} detections) — untoggle "watch" to finish`;
    el.className = "phase followup";
    $("#commit").textContent = "Commit follow-up";
    return;
  }
  if (!CFG.blind) { el.textContent = "unblinded labeling"; el.className = "phase"; }
  else if (!hasBlind) { el.textContent = "BLIND pass — broker opinions hidden"; el.className = "phase blind"; }
  else { el.textContent = "revealed — commit again to revise"; el.className = "phase revealed"; }
  $("#commit").textContent = hasBlind && CFG.blind ? "Commit revision" : "Commit";
}

function setLcMode(m) {
  lcMode = m;
  document.querySelectorAll("#lc-mode button").forEach((b) => b.classList.toggle("on", b.dataset.mode === m));
  if (cur) renderLightcurve(cur.evidence);
}

function renderLightcurve(ev) {
  const groups = {};
  for (const p of ev.points) {
    const key = `${p.survey}|${p.band}|${p.kind}`;
    (groups[key] ||= []).push(p);
  }
  const traces = [];
  for (const [key, pts] of Object.entries(groups)) {
    const [survey, band, kind] = key.split("|");
    const color = BAND_COLORS[band?.toLowerCase()] || "#888";
    const symbol = { lsst: "circle", ztf: "square" }[survey];
    let xs = [], ys = [], err = [], data = [];
    for (const p of pts) {
      let y = null, e = null;
      if (lcMode === "flux") {
        if (kind === "upper_limit") continue;
        y = p.diff_flux; e = p.diff_flux_err;
      } else if (lcMode === "mag") {
        y = kind === "upper_limit" ? p.lim_mag : p.mag; e = kind === "upper_limit" ? null : p.mag_err;
      } else {
        y = p.sci_mag; // ZTF: approximate ref + diff, only when the ref source is the object
        e = p.sci_mag_err;
      }
      if (y === null || y === undefined) continue;
      xs.push(p.mjd); ys.push(y); err.push(e); data.push(p);
    }
    if (!xs.length) continue;
    const open = kind !== "detection";
    traces.push({
      x: xs, y: ys, customdata: data.map((p) => p.alert_id || ""),
      error_y: kind === "upper_limit" ? undefined : { array: err, visible: true, thickness: 1, width: 0, color },
      mode: "markers", type: "scatter",
      name: `${survey.toUpperCase()} ${band}${kind === "forced" ? " (forced)" : kind === "upper_limit" ? " (lim)" : ""}${lcMode === "sci" && survey === "ztf" ? " (ref+diff)" : ""}`,
      marker: {
        color, size: kind === "upper_limit" ? 7 : 8,
        symbol: kind === "upper_limit" ? "triangle-down-open" : open ? `${symbol}-open` : symbol,
        line: { width: 1, color },
      },
      opacity: kind === "detection" ? 1 : 0.55,
      hovertemplate: `%{x:.3f}<br>%{y:.3g}<extra>${survey} ${band} ${kind}</extra>`,
    });
  }
  const css = getComputedStyle(document.documentElement);
  const ink = css.getPropertyValue("--ink"), line = css.getPropertyValue("--line");
  const layout = {
    margin: { l: 55, r: 10, t: 10, b: 40 },
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: ink, size: 11 },
    xaxis: { range: defaultTimeRange(traces), ...timeAxisFormat(defaultTimeRange(traces)), gridcolor: line, zeroline: false },
    yaxis: {
      title: { flux: "difference flux (nJy)", mag: "difference mag (AB)", sci: "total mag (LSST sci; ZTF ref+diff)" }[lcMode],
      autorange: lcMode === "flux" ? true : "reversed", gridcolor: line, zerolinecolor: ink, zeroline: lcMode === "flux",
    },
    legend: { orientation: "h", y: -0.18 },
  };
  $("#lc").innerHTML = "";
  if (!traces.length) { $("#lc").innerHTML = `<div class="loading">No points in this view.</div>`; return; }
  Plotly.newPlot("lc", traces, layout, { displaylogo: false, responsive: true });
  // keep the "Range: (date — date)" title and tick precision in step with zooming
  $("#lc").on("plotly_relayout", (ev) => {
    if ("xaxis.autorange" in ev) {
      // double-click reset: back to the default window, not Plotly's tight autorange
      const r = defaultTimeRange(traces);
      Plotly.relayout("lc", { "xaxis.range": r, ...timeAxisFormat(r, "xaxis.") });
    } else if ("xaxis.range[0]" in ev || "xaxis.range" in ev) {
      Plotly.relayout("lc", timeAxisFormat($("#lc").layout.xaxis.range, "xaxis."));
    }
  });
  $("#lc").on("plotly_click", (d) => {
    const id = d.points[0].customdata;
    const p = cur.evidence.points.find((q) => q.alert_id === id);
    if (p) selectPoint(p);
  });
}

const MIN_TIME_RANGE = 1; // MJD; short light curves are shown in at least a 1-day window, like ANTARES

function defaultTimeRange(traces) {
  const xs = traces.flatMap((t) => t.x);
  const lo = Math.min(...xs), hi = Math.max(...xs);
  const pad = Math.max((hi - lo) * 0.05, (MIN_TIME_RANGE - (hi - lo)) / 2);
  return [lo - pad, hi + pad];
}

const mjdToDate = (mjd) => new Date((mjd - 40587) * 86400000).toISOString().slice(0, 10);

function timeAxisFormat([lo, hi], prefix = "") {
  const decimals = Math.min(3, Math.max(0, Math.ceil(-Math.log10((hi - lo) / 8))));
  return {
    [`${prefix}title`]: { text: `Time (MJD) Range: (${mjdToDate(lo)} — ${mjdToDate(hi)})` },
    [`${prefix}tickformat`]: `,.${decimals}f`,
    [`${prefix}hoverformat`]: ",.4f",
  };
}

async function selectPoint(p) {
  selectedPoint = p;
  const d = [
    `<b>${p.survey.toUpperCase()} ${p.band}</b> MJD ${fmt(p.mjd, 4)}`,
    `diff flux ${fmt(p.diff_flux, 0)} ± ${fmt(p.diff_flux_err, 0)} nJy`,
    p.mag ? `mag ${fmt(p.mag)} ± ${fmt(p.mag_err)}` : p.is_negative ? `<span class="pill warn">negative difference</span>` : "",
    p.snr !== undefined ? `S/N ${fmt(p.snr, 1)}` : "",
    p.pixel_flags?.length ? p.pixel_flags.map((f) => `<span class="pill warn">${esc(f)}</span>`).join("") : "",
    p.ss_object_id ? `<span class="pill warn">Rubin ssObjectId ${p.ss_object_id}</span>` : "",
    p.ss_name ? `<span class="pill warn">ZTF ssnamenr ${esc(p.ss_name)} @ ${fmt(p.ss_dist_arcsec, 1)}″</span>` : "",
    p.distnr !== undefined && p.distnr !== null ? `ZTF distnr ${fmt(p.distnr, 1)}″ magnr ${fmt(p.magnr)}` : "",
    p.sgscore1 !== undefined && p.sgscore1 !== null ? `PS1 sgscore ${fmt(p.sgscore1)} @ ${fmt(p.distpsnr1, 1)}″` : "",
  ].filter(Boolean);
  $("#point-detail").innerHTML = d.join(" · ");
  $("#cut-meta").textContent = p.alert_id || "";
  if (!p.alert_id) { $("#cutouts").innerHTML = `<span class="muted">No cutouts for forced photometry.</span>`; return; }
  let th = cur.evidence.thumbs[p.alert_id];
  if (!th) {
    $("#cutouts").innerHTML = `<span class="muted">loading cutouts…</span>`;
    try { th = await api(`/api/thumbs/${encodeURIComponent(p.alert_id)}`); cur.evidence.thumbs[p.alert_id] = th; }
    catch { th = {}; }
  }
  if (selectedPoint !== p) return;
  const order = ["science", "template", "difference"];
  $("#cutouts").innerHTML = order.filter((k) => th[k]).map((k) => `<figure><img src="${th[k]}" alt="${k}"><figcaption>${k}</figcaption></figure>`).join("")
    || `<span class="muted">No cutouts available.</span>`;
}

// ---------- field + crossmatch ----------

function toPx(d_east, d_north) {
  const scale = FIELD_PX / fieldFov; // display px per arcsec
  return [FIELD_PX / 2 - d_east * scale, FIELD_PX / 2 - d_north * scale, scale];
}

function zoom(step) {
  const i = FOV_LEVELS.indexOf(fieldFov);
  const j = Math.min(FOV_LEVELS.length - 1, Math.max(0, (i < 0 ? FOV_LEVELS.indexOf(DEFAULT_FOV) : i) + step));
  if (FOV_LEVELS[j] === fieldFov) return;
  fieldFov = FOV_LEVELS[j];
  if (cur) renderField(cur.evidence);
  if (aladin) aladin.setFoV(fieldFov / 3600);
}

function renderField(ev) {
  const xm = ev.xmatch || {};
  const pixscale = (fieldFov / LS_SIZE).toFixed(4);
  const url = `https://www.legacysurvey.org/viewer/cutout.jpg?ra=${ev.ra}&dec=${ev.dec}&layer=ls-dr10&pixscale=${pixscale}&size=${LS_SIZE}`;
  $("#fov").textContent = `${fieldFov}″`;
  $("#zoom-in").disabled = fieldFov === FOV_LEVELS[0];
  $("#zoom-out").disabled = fieldFov === FOV_LEVELS[FOV_LEVELS.length - 1];
  const chosen = form.host && form.host.ls_id;
  let svg = "";
  const scale = toPx(0, 0)[2];
  for (const s of xm.legacy_survey || []) {
    const [x, y] = toPx(s.d_east, s.d_north);
    if (s.type === "PSF" || !s.a) {
      svg += `<circle class="src" data-ls="${s.ls_id}" cx="${x}" cy="${y}" r="5" fill="none" stroke="#6cf" stroke-width="1.2"><title>LS ${s.type} r=${fmt(s.mag_r)}</title></circle>`;
    } else {
      const isChosen = String(s.ls_id) === String(chosen);
      svg += `<ellipse class="src" data-ls="${s.ls_id}" cx="${x}" cy="${y}" rx="${Math.max(s.b * scale, 3)}" ry="${Math.max(s.a * scale, 3)}"
        transform="rotate(${-s.pa_deg} ${x} ${y})" fill="${isChosen ? "rgba(255,210,0,.25)" : "none"}" stroke="${isChosen ? "#ffd200" : "#f6a"}" stroke-width="${isChosen ? 2.5 : 1.3}">
        <title>LS ${s.type} r=${fmt(s.mag_r)} zphot=${fmt(s.z_phot_median)} dDLR=${fmt(s.d_dlr)}</title></ellipse>`;
    }
  }
  (xm.host_candidates || []).forEach((h, i) => {
    const [x, y] = toPx(h.d_east, h.d_north);
    svg += `<text class="rank" x="${x + 7}" y="${y - 7}">${i + 1}</text>`;
  });
  const chosenSrc = (xm.legacy_survey || []).find((q) => String(q.ls_id) === String(chosen));
  if (chosenSrc) {
    const [x, y] = toPx(chosenSrc.d_east, chosenSrc.d_north);
    svg += `<circle cx="${x}" cy="${y}" r="13" fill="none" stroke="#ffd200" stroke-width="2.5"/>`;
  }
  for (const g of xm.gaia || []) {
    const [x, y] = toPx(g.d_east, g.d_north);
    const c = g.likely_star ? "#ff4" : "#bb8";
    svg += `<path d="M${x - 6} ${y}h12M${x} ${y - 6}v12" stroke="${c}" stroke-width="1.5"><title>Gaia G=${fmt(g.Gmag)} plxSNR=${fmt(g.plx_snr, 1)} pmSNR=${fmt(g.pm_snr, 1)}</title></path>`;
  }
  const c = FIELD_PX / 2;
  if (xm.radius_arcsec && xm.radius_arcsec * scale < FIELD_PX * 0.7) {
    svg += `<circle cx="${c}" cy="${c}" r="${xm.radius_arcsec * scale}" fill="none" stroke="#fff" stroke-opacity=".35" stroke-dasharray="2 4"><title>crossmatch radius ${xm.radius_arcsec}″</title></circle>`;
  }
  svg += `<circle cx="${c}" cy="${c}" r="9" fill="none" stroke="#0f0" stroke-width="1.5" stroke-dasharray="3 2"/>`;
  svg += `<text x="6" y="14" fill="#fff" font-size="11">N↑ E←  ${fieldFov}″</text>`;
  svg += `<circle id="hover-ring" r="12" fill="none" stroke="#fff" stroke-width="1.5" visibility="hidden" pointer-events="none"/>`;
  $("#field").innerHTML = `<img src="${url}" alt="Legacy Survey cutout"><svg viewBox="0 0 ${FIELD_PX} ${FIELD_PX}">${svg}</svg>`;
  attachHostPicker(xm);
  $("#field-legend").innerHTML = `
    <div><i style="border-color:#0f0"></i>alert position</div>
    <div><i style="border-color:#f6a"></i>LS galaxy (half-light ellipse)</div>
    <div><i style="border-color:#6cf"></i>LS point source</div>
    <div><i style="border-color:#ff4;border-radius:0"></i>Gaia (bright = sig. plx/pm)</div>
    <div><i style="border-color:#ffd200;background:rgba(255,210,0,.3)"></i>your host</div>`;
}

// ---------- detection offsets (ANTARES-style polar plot) ----------

function renderAstrometry(ev) {
  const dets = ev.points.filter((p) => p.kind === "detection" && p.ra !== undefined && p.ra !== null);
  if (!dets.length) { $("#astro").innerHTML = `<div class="muted" style="font-size:12px">no per-detection positions</div>`; return; }
  // reference = mean LSST position (LSST astrometry is the sharper of the two)
  const ref = dets.some((p) => p.survey === "lsst") ? dets.filter((p) => p.survey === "lsst") : dets;
  const ra0 = ref.reduce((s, p) => s + p.ra, 0) / ref.length, dec0 = ref.reduce((s, p) => s + p.dec, 0) / ref.length;
  const cosd = Math.cos((dec0 * Math.PI) / 180);
  const off = dets.map((p) => {
    const e = (p.ra - ra0) * cosd * 3600, n = (p.dec - dec0) * 3600;
    return { p, e, n, r: Math.hypot(e, n), theta: (Math.atan2(e, n) * 180) / Math.PI }; // PA, north through east
  });
  const rmax = Math.max(1, ...off.map((o) => o.r)) * 1.15;
  const tmin = Math.min(...dets.map((p) => p.mjd)), tmax = Math.max(...dets.map((p) => p.mjd));
  const css = getComputedStyle(document.documentElement);
  const ink = css.getPropertyValue("--ink"), line = css.getPropertyValue("--line");
  const traces = ["lsst", "ztf"].map((s) => {
    const o = off.filter((x) => x.p.survey === s);
    return {
      type: "scatterpolar", mode: "markers", name: s.toUpperCase(),
      r: o.map((x) => x.r), theta: o.map((x) => x.theta), customdata: o.map((x) => x.p.alert_id),
      text: o.map((x) => `MJD ${x.p.mjd.toFixed(4)} · ${x.p.band} · ${x.r.toFixed(2)}″`),
      hovertemplate: "%{text}<extra></extra>",
      marker: {
        size: 8, symbol: s === "lsst" ? "circle" : "square",
        color: o.map((x) => x.p.mjd), cmin: tmin, cmax: tmax === tmin ? tmin + 1 : tmax, colorscale: "Viridis",
        showscale: s === "lsst", colorbar: { title: { text: "MJD", side: "right" }, thickness: 8, len: 0.8, tickformat: ".1f", x: 1.12 },
      },
    };
  }).filter((t) => t.r.length);
  Plotly.newPlot("astro", traces, {
    margin: { l: 30, r: 70, t: 24, b: 24 }, showlegend: false,
    paper_bgcolor: "rgba(0,0,0,0)", font: { color: ink, size: 10 },
    polar: {
      bgcolor: "rgba(0,0,0,0)",
      radialaxis: { range: [0, rmax], angle: 90, ticksuffix: "″", gridcolor: line, linecolor: line, dtick: rmax > 3 ? undefined : 0.5 },
      // north up, east left (sky view): PA increases counterclockwise from the top
      angularaxis: { rotation: 90, direction: "counterclockwise", tickvals: [0, 90, 180, 270], ticktext: ["DEC+", "RA+", "DEC−", "RA−"], gridcolor: line, linecolor: line },
    },
  }, { displaylogo: false, displayModeBar: false, responsive: true });
  $("#astro").on("plotly_click", (d) => {
    const p = cur.evidence.points.find((q) => q.alert_id === d.points[0].customdata);
    if (p) selectPoint(p);
  });
  $("#astro-meta").textContent = `${dets.length} detections, relative to mean ${ref === dets ? "" : "LSST "}position`;
  $("#astro-summary").innerHTML = astrometrySummary(off, ref.length);
}

function astrometrySummary(off, nRef) {
  const lsst = off.filter((o) => o.p.survey === "lsst");
  const rms = lsst.length > 1 ? Math.sqrt(lsst.reduce((s, o) => s + o.r * o.r, 0) / lsst.length) : null;
  const errs = lsst.map((o) => o.p.pos_err_arcsec).filter((e) => e);
  const medErr = errs.length ? errs.sort((a, b) => a - b)[Math.floor(errs.length / 2)] : null;
  let html = `max offset <b>${fmt(Math.max(...off.map((o) => o.r)), 2)}″</b>`;
  if (rms !== null) html += `<br>LSST rms scatter <b>${fmt(rms, 2)}″</b>` + (medErr ? ` <span class="muted">(median pos. err ${fmt(medErr, 3)}″)</span>` : "");
  // apparent motion: straight-line fit of offset vs time within the same night
  const last = Math.max(...off.map((o) => o.p.mjd));
  const night = off.filter((o) => last - o.p.mjd < 0.5);
  if (night.length >= 2) {
    const fit = (key) => {
      const tm = night.reduce((s, o) => s + o.p.mjd, 0) / night.length, ym = night.reduce((s, o) => s + o[key], 0) / night.length;
      const sxx = night.reduce((s, o) => s + (o.p.mjd - tm) ** 2, 0);
      return sxx > 0 ? night.reduce((s, o) => s + (o.p.mjd - tm) * (o[key] - ym), 0) / sxx : 0;
    };
    const span_h = (Math.max(...night.map((o) => o.p.mjd)) - Math.min(...night.map((o) => o.p.mjd))) * 24;
    if (span_h > 0.05) {
      const rate = Math.hypot(fit("e"), fit("n")) / 24; // arcsec / hour
      const moving = rate * span_h > 3 * (medErr || 0.05) && rate > 0.5;
      html += `<br>same-night motion <b>${fmt(rate, 2)}″/hr</b> over ${fmt(span_h, 2)} h` +
        (moving ? ` <span class="pill warn">consistent with a mover</span>` : "");
    }
  }
  html += `<div class="muted" style="margin-top:6px">A real stationary source clusters near the center; an asteroid
    shows a line of points ordered in time (color). Click a point to see its cutouts.</div>`;
  return html;
}

// ---------- Aladin Lite (loaded on first use) ----------

const ALADIN_JS = "https://cdn.jsdelivr.net/npm/aladin-lite@3.8.2/dist/aladin.js";
let aladin = null, aladinReady = null, aladinCats = [];

function loadAladin() {
  // the npm build is an ES module (default export = the "A" API object)
  aladinReady ||= import(ALADIN_JS).then(async (mod) => {
    window.A = mod.default;
    await A.init;
    aladin = A.aladin("#aladin", {
      survey: $("#aladin-survey").value, fov: DEFAULT_FOV / 3600, showReticle: true,
      showCooGrid: false, showProjectionControl: false, showFullscreenControl: true, showLayersControl: true,
    });
    aladin.on("objectClicked", (obj) => {
      const id = obj && obj.data && obj.data.ls_id;
      const s = id && (cur?.evidence.xmatch?.legacy_survey || []).find((q) => String(q.ls_id) === id);
      if (s) setHost(hostFrom(s));
    });
    return aladin;
  });
  return aladinReady;
}

function setAladinSurvey(id) {
  (aladin.setBaseImageLayer || aladin.setImageSurvey).call(aladin, id);
}

async function updateAladin(ev) {
  try { await loadAladin(); } catch (e) {
    $("#aladin").innerHTML = `<div class="err" style="padding:12px">Aladin Lite could not start: ${esc(e.message || e)}.
      It needs WebGL2 (current Chrome / Firefox / Safari). The LS DR10 panel still works.</div>`;
    return;
  }
  if (cur?.evidence !== ev) return;
  aladin.gotoRaDec(ev.ra, ev.dec);
  aladin.setFoV(fieldFov / 3600);
  if (aladin.removeLayers) aladin.removeLayers(); // clears overlays (catalogs), not the image layer
  aladinCats = [];
  const xm = ev.xmatch || {};
  const chosen = form.host && String(form.host.ls_id);
  const mk = (name, color, shape, items) => {
    if (!items.length) return;
    const cat = A.catalog({ name, color, shape, sourceSize: 14, onClick: "showPopup" });
    aladin.addCatalog(cat);
    cat.addSources(items);
    aladinCats.push(cat);
  };
  const ls = xm.legacy_survey || [];
  mk("LS DR10 galaxies", "#ff66aa", "circle", ls.filter((s) => s.type !== "PSF" && String(s.ls_id) !== chosen)
    .map((s) => A.source(s.ra, s.dec, { name: `LS ${s.type}`, r: fmt(s.mag_r), z_phot: fmt(s.z_phot_median), dDLR: fmt(s.d_dlr), ls_id: String(s.ls_id) })));
  mk("LS DR10 point sources", "#66ccff", "circle", ls.filter((s) => s.type === "PSF" && String(s.ls_id) !== chosen)
    .map((s) => A.source(s.ra, s.dec, { name: "LS PSF", r: fmt(s.mag_r), ls_id: String(s.ls_id) })));
  mk("Gaia DR3", "#ffff44", "plus", (xm.gaia || []).map((g) => A.source(g.RA_ICRS, g.DE_ICRS, { name: "Gaia", G: fmt(g.Gmag), plx_snr: fmt(g.plx_snr, 1), pm_snr: fmt(g.pm_snr, 1) })));
  const host = ls.find((s) => String(s.ls_id) === chosen);
  if (host) mk("your host", "#ffd200", "square", [A.source(host.ra, host.dec, { name: "host", ls_id: String(host.ls_id) })]);
}

const PICK_RADIUS_PX = 30;

// Click/hover anywhere on the field picks the nearest Legacy Survey source,
// so tiny ellipses don't have to be hit exactly.
function attachHostPicker(xm) {
  const svgEl = $("#field svg");
  const ring = $("#hover-ring");
  const sources = (xm.legacy_survey || []).map((s) => ({ s, px: toPx(s.d_east, s.d_north) }));
  const nearest = (e) => {
    const r = svgEl.getBoundingClientRect();
    const mx = ((e.clientX - r.left) / r.width) * FIELD_PX, my = ((e.clientY - r.top) / r.height) * FIELD_PX;
    let best = null, bestD = PICK_RADIUS_PX;
    for (const o of sources) {
      const d = Math.hypot(o.px[0] - mx, o.px[1] - my);
      if (d < bestD) { best = o; bestD = d; }
    }
    return best;
  };
  svgEl.onmousemove = (e) => {
    const o = nearest(e);
    if (!o) { ring.setAttribute("visibility", "hidden"); $("#field-hover").innerHTML = "&nbsp;"; return; }
    ring.setAttribute("cx", o.px[0]); ring.setAttribute("cy", o.px[1]); ring.setAttribute("visibility", "visible");
    const s = o.s, rank = (xm.host_candidates || []).findIndex((h) => String(h.ls_id) === String(s.ls_id));
    $("#field-hover").innerHTML = `${rank >= 0 ? `<b>#${rank + 1}</b> ` : ""}LS <b>${esc(s.type)}</b> · sep ${fmt(s.sep, 1)}″ · dDLR ${fmt(s.d_dlr)} · r ${fmt(s.mag_r)} · z_phot ${fmt(s.z_phot_median)}${s.z_spec ? ` · z_spec ${fmt(s.z_spec, 4)}` : ""} — click to set host`;
  };
  svgEl.onmouseleave = () => { ring.setAttribute("visibility", "hidden"); $("#field-hover").innerHTML = "&nbsp;"; };
  svgEl.onclick = (e) => {
    const o = nearest(e);
    if (!o) return;
    const same = form.host && String(form.host.ls_id) === String(o.s.ls_id);
    setHost(same ? null : hostFrom(o.s)); // clicking the chosen host again clears it
  };
}

function hostFrom(s) {
  return { catalog: "ls_dr10", ls_id: String(s.ls_id), ra: s.ra, dec: s.dec, sep: s.sep, d_dlr: s.d_dlr, z_phot_median: s.z_phot_median, z_spec: s.z_spec, type: s.type };
}

function table(rows, cols, opts = {}) {
  if (!rows || !rows.length) return `<div class="muted" style="font-size:12px">none within radius</div>`;
  const head = cols.map((c) => `<th>${c[0]}</th>`).join("");
  const body = rows.map((r, i) => {
    const cls = opts.rowClass ? opts.rowClass(r) : "";
    return `<tr class="${cls}" data-i="${i}">${cols.map((c) => `<td>${typeof c[1] === "function" ? c[1](r) : esc(fmt(r[c[1]], c[2] ?? 2))}</td>`).join("")}</tr>`;
  }).join("");
  return `<div class="tablewrap"><table data-t="${opts.id || ""}"><tr>${head}</tr>${body}</table></div>`;
}

function renderXmatch(ev) {
  const xm = ev.xmatch;
  if (!xm) { $("#xmatch").innerHTML = `<div class="muted">no crossmatch</div>`; return; }
  const ctx = xm.context || {};
  const sso = ev.sso_associations || {};
  const ssoBits = [
    ...(sso.lsst_ssObjectId || []).map((s) => `<span class="pill warn">Rubin ssObjectId ${s}</span>`),
    ...(sso.ztf_ssnamenr || []).map((s) => `<span class="pill warn">ZTF ssnamenr ${esc(s)}</span>`),
  ];
  let html = `<div style="font-size:12px">
    <span class="pill">radius ${fmt(xm.radius_arcsec, 0)}″</span><span class="pill">b =${fmt(ctx.gal_b, 1)}°</span><span class="pill ${Math.abs(ctx.ecl_lat) < 10 ? "warn" : ""}">ecl. lat = ${fmt(ctx.ecl_lat, 1)}°</span>
    ${ssoBits.join("") || `<span class="pill">no pipeline SSO association</span>`}
    ${ev.ztf_object_id ? `<span class="pill">ANTARES ZTF assoc: ${esc(ev.ztf_object_id)}</span>` : ""}
  </div>`;
  html += `<div id="skybot-out" style="font-size:12px">${renderSkybot(ev.skybot)}</div>`;
  const chosen = form.host && form.host.ls_id;
  html += `<h3>Host candidates (LS DR10, ranked by d<sub>DLR</sub>)</h3>` +
    table(xm.host_candidates, [
      ["ls_id", (r) => String(r.ls_id).slice(-7)], ["type", "type"], ["sep″", "sep", 1], ["dDLR", "d_dlr", 2],
      ["r", "mag_r", 2], ["z_phot", (r) => `${fmt(r.z_phot_median)} <span class="muted">[${fmt(r.z_phot_l68)}–${fmt(r.z_phot_u68)}]</span>`],
      ["z_spec", "z_spec", 4],
    ], { id: "hosts", rowClass: (r) => "clickable" + (String(r.ls_id) === String(chosen) ? " chosen" : "") });
  html += `<h3>Gaia DR3</h3>` + table(xm.gaia, [
    ["sep″", "sep", 1], ["G", "Gmag", 2], ["BP-RP", "BP-RP", 2], ["plx S/N", "plx_snr", 1], ["pm S/N", "pm_snr", 1], ["RUWE", "RUWE", 2],
    ["star?", (r) => (r.likely_star ? `<span class="pill warn">yes</span>` : "")],
  ]);
  html += `<h3>ZTF objects (ALeRCE position search, ≤30″)</h3>`;
  if (xm.ztf === undefined) {
    html += `<div class="muted" style="font-size:12px">not searched for this object yet — click Search above</div>`;
  } else {
    const antZ = ev.ztf_object_id;
    const near = (xm.ztf || []).filter((o) => o.sep <= 2);
    if (near.length && !near.some((o) => o.oid === antZ)) html += `<span class="pill warn">ZTF object within 2″ that ANTARES did not associate</span>`;
    if (antZ && xm.ztf && !xm.ztf.some((o) => o.oid === antZ)) html += `<span class="pill warn">ANTARES's ZTF assoc ${esc(antZ)} not found within radius</span>`;
    html += table(xm.ztf, [
      ["sep″", "sep", 1], ["oid", (r) => `<a target="_blank" href="https://alerce.online/object/${esc(r.oid)}">${esc(r.oid)}</a>`],
      ["ndet", "ndet", 0], ["ndethist", "ndethist", 0], ["first", "firstmjd", 1], ["last", "lastmjd", 1],
      ["", (r) => (r.oid === antZ ? `<span class="pill">ANTARES assoc</span>` : "")],
    ]);
  }
  html += `<h3>Milliquas</h3>` + table(xm.milliquas, [["sep″", "sep", 1], ["name", "Name"], ["type", "Type"], ["z", "z", 3], ["Qpct", "Qpct", 0]]);
  html += `<h3>SIMBAD</h3>` + table(xm.simbad, [["sep″", "sep", 1], ["id", "main_id"], ["otype", "otype"], ["z", "rvz_redshift", 4]]);
  html += `<h3>NED</h3>` + table(xm.ned, [["sep″", "sep", 1], ["name", "name"], ["type", "type"], ["z", "z", 4]]);
  html += `<h3>PS1</h3>` + table(xm.ps1, [["sep″", "sep", 1], ["g", "gmag"], ["r", "rmag"], ["i", "imag"], ["z", "zmag"]]);
  const errs = Object.entries(xm.errors || {});
  if (errs.length) html += `<div class="err">${errs.map(([k, v]) => `${k}: ${esc(v)}`).join("<br>")}</div>`;
  $("#xmatch").innerHTML = html;
  $("#xmatch").querySelectorAll('table[data-t="hosts"] tr[data-i]').forEach((tr) => (tr.onclick = () => {
    setHost(hostFrom(xm.host_candidates[+tr.dataset.i]));
    renderXmatch(ev);
  }));
}

function renderSkybot(sb) {
  if (!sb) return "";
  const parts = [];
  for (const k of ["first", "last"]) {
    if (sb[k] === undefined) continue;
    const rows = sb[k];
    parts.push(rows === null ? `SkyBoT ${k}: <span class="err">${esc(sb.errors?.[k] || "failed")}</span>`
      : rows.length ? `SkyBoT ${k} epoch: ` + rows.slice(0, 3).map((r) => `<span class="pill warn">${esc(r.Name)} V=${fmt(r.V, 1)} @ ${fmt(r.sep, 1)}″</span>`).join("")
      : `SkyBoT ${k} epoch: no known SSO within 60″`);
  }
  return parts.join("<br>");
}

async function searchXmatch() {
  if (!cur) return;
  const radius = +$("#xm-radius").value;
  if (!(radius > 0)) return;
  $("#xm-search").disabled = true;
  $("#xmatch").innerHTML = `<div class="muted">searching ${radius}″…</div>`;
  try {
    cur.evidence.xmatch = await api(`/api/xmatch/${cur.evidence.object_id}?radius=${radius}`, { method: "POST" });
    // zoom out far enough to show the whole search area
    if (2 * radius > fieldFov) fieldFov = FOV_LEVELS.find((f) => f >= 2 * radius) || FOV_LEVELS[FOV_LEVELS.length - 1];
    renderXmatch(cur.evidence);
    renderField(cur.evidence);
  } catch (e) {
    $("#xmatch").innerHTML = `<div class="err">${esc(e.message)}</div>`;
  } finally {
    $("#xm-search").disabled = false;
  }
}

async function runSkybot() {
  if (!cur) return;
  $("#skybot").disabled = true;
  $("#skybot-out").innerHTML = `<span class="muted">querying SkyBoT (can be slow)…</span>`;
  try {
    cur.evidence.skybot = await api(`/api/skybot/${cur.evidence.object_id}`, { method: "POST" });
    $("#skybot-out").innerHTML = renderSkybot(cur.evidence.skybot);
  } catch (e) {
    $("#skybot-out").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  } finally {
    $("#skybot").disabled = false;
  }
}

// ---------- broker opinions ----------

function probBars(probs) {
  return Object.entries(probs).sort((a, b) => b[1] - a[1])
    .map(([k, p]) => `<div style="font-size:12px"><span style="display:inline-block;width:70px">${esc(k)}</span><span class="bar" style="width:${Math.round(p * 140)}px"></span> ${fmt(p, 3)}</div>`)
    .join("");
}

function renderOpinions() {
  const el = $("#opinions");
  if (!cur.revealed || !cur.opinions) {
    $("#op-state").textContent = "hidden";
    el.innerHTML = `<div class="hidden-note">Hidden until you commit a blind label: ALeRCE probabilities, LSST reliability / ZTF drb, ANTARES tags and catalog matches, Babamul flags and associations.</div>`;
    return;
  }
  $("#op-state").textContent = "";
  const op = cur.opinions;
  let html = `<h3>Real/bogus scores</h3><div style="font-size:12px">
    LSST reliability mean <b>${fmt(op.reliability.lsst_mean, 3)}</b> <span class="muted">(${op.reliability.lsst_per_alert.map((r) => fmt(r, 2)).join(", ")})</span><br>
    ZTF drb mean <b>${fmt(op.reliability.ztf_drb_mean, 3)}</b></div>`;
  const al = op.alerce || {};
  for (const survey of ["lsst", "ztf"]) {
    const clfs = al[survey];
    if (!clfs) { if (al.errors?.[survey]) html += `<h3>ALeRCE ${survey}</h3><div class="err">${esc(al.errors[survey]).slice(0, 200)}</div>`; continue; }
    for (const [name, e] of Object.entries(clfs)) {
      html += `<h3>ALeRCE ${survey}: ${esc(name)} <span class="muted">v${esc(e.version)}</span></h3>${probBars(e.probs)}`;
    }
  }
  const an = op.antares || {};
  html += `<h3>ANTARES</h3><div>${(an.tags || []).map((t) => `<span class="pill">${esc(t)}</span>`).join("") || `<span class="muted">no tags</span>`}</div>
    <div style="font-size:12px">catalogs: ${(an.catalogs || []).map(esc).join(", ") || "none"}</div>`;
  const bb = op.babamul || {};
  html += `<h3>Babamul</h3>`;
  if (!bb.enabled) html += `<div class="muted" style="font-size:12px">not configured (set BABAMUL_API_TOKEN in .env)</div>`;
  else if (!bb.found) html += `<div class="muted" style="font-size:12px">object not found ${esc(JSON.stringify(bb.errors || {}))}</div>`;
  else {
    const pr = bb.properties || {};
    html += `<div>${["rock", "star", "near_brightstar", "stationary"].map((k) => `<span class="pill ${pr[k] ? "warn" : ""}">${k}: ${pr[k] === undefined || pr[k] === null ? "—" : pr[k]}</span>`).join("")}</div>`;
    const zm = bb.ztf_match;
    const antZ = cur.evidence.ztf_object_id;
    html += `<div style="font-size:12px">ZTF match: ${zm ? esc(zm.objectId) : "none"}${(zm?.objectId || null) !== (antZ || null) ? ` <span class="pill warn">differs from ANTARES (${esc(antZ || "none")})</span>` : ""}</div>`;
    const cm = bb.cross_matches || {};
    const n = Object.entries(cm).filter(([, v]) => v && v.length).map(([k, v]) => `${k}: ${v.length}`).join(", ");
    html += `<div style="font-size:12px">crossmatches: ${esc(n || "none")}</div>`;
  }
  el.innerHTML = html;
}

// ---------- commit ----------

async function commit() {
  if (!cur || !form.klass) return;
  const body = {
    object_id: cur.evidence.object_id, klass: form.klass, alt_classes: [...form.alts], subclass: form.subclass,
    confidence: form.confidence, flags: [...form.flags], host: form.host, notes: form.notes || null,
  };
  try {
    const r = await api("/api/label", { method: "POST", body });
    toast(`Saved ${r.phase} label: ${form.klass}`);
    refreshStats();
    const oid = cur.evidence.object_id;
    if (r.phase === "blind") {
      // reveal broker opinions for the optional second look
      cur.labels.push({ phase: "blind", klass: form.klass });
      cur.opinions = await api(`/api/reveal/${oid}`);
      cur.revealed = true;
      renderPhase();
      renderOpinions();
      $("#opinions-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
    } else {
      next();
    }
  } catch (e) {
    toast("Save failed: " + e.message, 5000);
  }
}

init();
