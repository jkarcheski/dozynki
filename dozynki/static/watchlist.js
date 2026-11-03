// Watchlist: your objects flagged "watch", filtered by when they were flagged and by detections, latest mag and Dec;
// sortable by any column, with a CSV of the selected (or shown) rows.

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fix = (v, d) => (v == null ? "—" : v.toFixed(d));

let CLASSES = {};
let objects = [];
let shown = [];
let selected = new Set();
let sort = { key: "flagged_at", dir: -1 };

// [key, heading, cell, numeric]; sorting uses the row's value under key
const COLUMNS = [
  ["object_id", "Object", (o) => `<a href="/vet?object=${encodeURIComponent(o.object_id)}">${esc(o.object_id)}</a>`],
  ["klass", "Class", (o) => `${esc(CLASSES[o.klass] ?? o.klass)}${o.subclass ? ` <span class="muted">${esc(o.subclass)}</span>` : ""}`],
  ["confidence", "Confidence", (o) => esc(o.confidence)],
  ["n_det", "Detections", (o) => o.n_det ?? "—", true],
  ["last_mag", "Latest mag", (o) => (o.last_mag == null ? "—" : `${o.last_mag.toFixed(2)} <span class="muted">${esc(o.last_band)}</span>`), true],
  ["ra", "RA", (o) => fix(o.ra, 5), true],
  ["dec", "Dec", (o) => fix(o.dec, 5), true],
  ["flags", "Flags", (o) => JSON.parse(o.flags || "[]").filter((f) => f !== "watch").map((f) => `<span class="pill">${esc(f)}</span>`).join("")],
  ["notes", "Notes", (o) => esc(o.notes)],
  ["flagged_at", "Flagged (UTC)", (o) => `<span class="muted">${esc(o.flagged_at.slice(0, 16).replace("T", " "))}</span>`],
];
const CSV_COLUMNS = ["object_id", "antares_locus", "ra", "dec", "n_det", "last_mag", "last_band", "last_mjd", "klass",
  "subclass", "confidence", "flags", "notes", "phase", "flagged_at"];
const RANGES = [["n_det", "#ndet-min", "#ndet-max"], ["last_mag", "#mag-min", "#mag-max"], ["dec", "#dec-min", "#dec-max"]];

function toast(msg, ms = 3000) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), ms);
}

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
  return r.json();
}

// UTC date n days ago, as YYYY-MM-DD (labels are stamped in UTC)
const daysAgo = (n) => new Date(Date.now() - n * 86400000).toISOString().slice(0, 10);

function setRange(v) {
  document.querySelectorAll("#range button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
  $("#since").value = v === "night" ? daysAgo(1) : v === "week" ? daysAgo(7) : "";
  load();
}

async function load() {
  const since = $("#since").value;
  try {
    objects = (await api(`/api/watchlist${since ? `?since=${since}` : ""}`)).objects;
    $("#err").textContent = "";
    const ids = new Set(objects.map((o) => o.object_id));
    selected = new Set([...selected].filter((id) => ids.has(id)));
    render();
  } catch (e) {
    $("#err").textContent = e.message;
  }
}

// An object with no value for a filtered attribute is hidden: it can't be shown to be in range.
function passes(o) {
  return RANGES.every(([key, lo, hi]) => {
    const min = $(lo).value, max = $(hi).value;
    if (min === "" && max === "") return true;
    const v = o[key];
    return v != null && (min === "" || v >= +min) && (max === "" || v <= +max);
  });
}

function compare(a, b) {
  const x = a[sort.key], y = b[sort.key];
  if (x == null || y == null) return (x == null) - (y == null);  // missing values last either way
  return sort.dir * (typeof x === "number" ? x - y : String(x).localeCompare(String(y)));
}

function render() {
  shown = objects.filter(passes).sort(compare);
  const since = $("#since").value;
  $("#count").textContent = `· ${shown.length}${shown.length < objects.length ? ` of ${objects.length}` : ""} object${objects.length === 1 ? "" : "s"}`
    + (selected.size ? ` · ${selected.size} selected` : "");
  $("#csv").textContent = selected.size ? `Download CSV (${selected.size} selected)` : "Download CSV";
  if (!objects.length) {
    $("#list").innerHTML = `<tr><td class="muted">Nothing on your watchlist${since ? " flagged since " + since : ""}.</td></tr>`;
    return;
  }
  const all = shown.length && shown.every((o) => selected.has(o.object_id));
  $("#list").innerHTML =
    `<tr><th><input type="checkbox" id="select-all" aria-label="Select every row shown"${all ? " checked" : ""}></th>`
    + COLUMNS.map(([key, head, , num]) => `<th class="sortable${num ? " num" : ""}${sort.key === key ? " sorted" : ""}" data-k="${key}">`
      + `${head}${sort.key === key ? (sort.dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("") + `</tr>`
    + (shown.map((o) => `<tr${selected.has(o.object_id) ? ' class="sel"' : ""}>`
      + `<td><input type="checkbox" data-id="${esc(o.object_id)}" aria-label="Select ${esc(o.object_id)}"${selected.has(o.object_id) ? " checked" : ""}></td>`
      + COLUMNS.map(([, , cell, num]) => `<td${num ? ' class="num"' : ""}>${cell(o)}</td>`).join("") + `</tr>`).join("")
      || `<tr><td></td><td colspan="${COLUMNS.length}" class="muted">No watched objects match these filters.</td></tr>`);
}

function onTableClick(e) {
  const th = e.target.closest("th.sortable");
  if (th) {
    sort = th.dataset.k === sort.key ? { key: sort.key, dir: -sort.dir } : { key: th.dataset.k, dir: 1 };
    return render();
  }
  const box = e.target.closest("input[type=checkbox]");
  if (!box) return;
  const ids = box.id === "select-all" ? shown.map((o) => o.object_id) : [box.dataset.id];
  ids.forEach((id) => (box.checked ? selected.add(id) : selected.delete(id)));
  render();
}

function csvCell(v) {
  const s = String(v ?? "");
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function downloadCsv() {
  const rows = selected.size ? shown.concat(objects.filter((o) => !shown.includes(o))).filter((o) => selected.has(o.object_id)) : shown;
  if (!rows.length) return toast("No rows to download.");
  const text = [CSV_COLUMNS.join(","), ...rows.map((o) => CSV_COLUMNS.map((k) => csvCell(o[k])).join(","))].join("\n") + "\n";
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const since = $("#since").value;
  a.download = `watchlist${since ? "_since_" + since : ""}${selected.size ? "_selected" : ""}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

async function recheck() {
  $("#recheck").disabled = true;
  toast("Rechecking watched objects…", 10000);
  try {
    const r = await api("/api/watch/recheck", { method: "POST" });
    const errs = Object.keys(r.errors).length;
    toast(`${r.requeued.length} watched object(s) have new alerts and are back in the queue; ${r.unchanged} unchanged` + (errs ? `; ${errs} failed` : ""), 6000);
  } catch (e) {
    toast(e.message, 6000);
  } finally {
    $("#recheck").disabled = false;
    load();
  }
}

async function init() {
  const cfg = await api("/api/config");
  CLASSES = Object.fromEntries(cfg.classes.map((c) => [c.key, c.label]));
  document.querySelectorAll("#range button").forEach((b) => (b.onclick = () => setRange(b.dataset.v)));
  $("#since").onchange = () => {
    document.querySelectorAll("#range button").forEach((b) => b.classList.remove("on"));
    load();
  };
  RANGES.forEach(([, lo, hi]) => [lo, hi].forEach((s) => ($(s).oninput = render)));
  $("#clear-filters").onclick = () => {
    RANGES.forEach(([, lo, hi]) => ($(lo).value = $(hi).value = ""));
    render();
  };
  $("#list").onclick = onTableClick;
  $("#csv").onclick = downloadCsv;
  $("#recheck").onclick = recheck;
  setRange("night");
}

init().catch((e) => ($("#err").textContent = e.message));
