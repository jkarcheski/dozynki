// Daily page: your daily jobs' reports, newest first, and the settings for which jobs run and when.

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const n = (v) => (v == null ? "—" : v.toLocaleString());
const vet = (id, text = id) => `<a href="/vet?object=${encodeURIComponent(id)}">${esc(text)}</a>`;
const SHOW_IDS = 12;  // object links per job before "and N more"

let state = null;
let poll = null;

function toast(msg, ms = 3000) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), ms);
}

async function api(path, opts = {}) {
  if (opts.body) opts = { ...opts, headers: { "Content-Type": "application/json" }, body: JSON.stringify(opts.body) };
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = (await r.text()).slice(0, 300);
    try { msg = JSON.parse(msg).detail; } catch {}
    throw new Error(msg);
  }
  return r.json();
}

function ids(list, label) {
  if (!list.length) return "";
  const more = list.length > SHOW_IDS ? ` and ${list.length - SHOW_IDS} more` : "";
  return `<div class="ids">${list.slice(0, SHOW_IDS).map(label).join(" · ")}${more}</div>`;
}

function filterItem(f) {
  if (f.error) return `<li>Filter <b>${esc(f.name)}</b>: failed (${esc(f.error)})</li>`;
  return `<li>Filter <b>${esc(f.name)}</b>: ${n(f.n_passed)} passed the listing rules, ${n(f.filtered)} thrown out by the full check;
    <b>${n(f.left)}</b> left to vet${f.screening ? `, ${n(f.screening)} still being checked` : ""}${f.vetted ? `, ${n(f.vetted)} vetted before` : ""}.
    ${f.left ? `Vet them in <a href="/vet">Filtered mode</a> with this filter.` : ""}</li>`;
}

function watchItem(w) {
  if (w.error) return `<li>Watchlist: recheck failed (${esc(w.error)})</li>`;
  const errs = Object.keys(w.errors).length;
  return `<li>Watchlist: <b>${w.requeued.length}</b> of ${w.requeued.length + w.unchanged} watched objects have new alerts
    and are back in your queue${errs ? `; ${errs} couldn't be checked` : ""}.
    ${ids(w.requeued, (r) => vet(r.object_id) + ` <span class="muted">+${r.new_alerts}</span>`)}</li>`;
}

function snItem(s) {
  if (s.error) return `<li>ALeRCE SN: failed (${esc(s.error)})</li>`;
  return `<li>ALeRCE SN: ${n(s.matched)} LSST objects first seen in the last ${s.sn_days} nights have SN on top
    (p ≥ ${s.sn_min_prob}, ${s.sn_min_det}+ detections); ${n(s.new)} new, ${s.added.length} added to the list
    <b>${esc(s.list)}</b>, <b>${n(s.to_vet)}</b> to vet${s.missing ? ` (${s.missing} not found in ANTARES)` : ""}.
    Vet them in List mode, or one by one: ${ids(s.added, (a) => vet(a.object_id) + ` <span class="muted">${a.p}</span>`)}</li>`;
}

function renderReport(r) {
  const when = `<span class="muted">${esc((r.finished_at || r.started_at).slice(0, 16).replace("T", " "))} UTC</span>`;
  if (r.state === "running") return `<div class="report"><h3>Running the night of ${esc(r.night)}… ${when}</h3>
    <div class="hint">A filter waits for its background check, which can take a while on a big night.</div></div>`;
  const rep = r.report || {};
  const items = [];
  if (rep.n_night != null) items.push(`<li>${n(rep.n_night)} LSST objects alerted on the night of ${esc(rep.night)}.</li>`);
  else if (rep.night_error) items.push(`<li>Couldn't count the night in ANTARES (${esc(rep.night_error)}).</li>`);
  (rep.filters || []).forEach((f) => items.push(filterItem(f)));
  if (rep.watchlist) items.push(watchItem(rep.watchlist));
  if (rep.alerce_sn) items.push(snItem(rep.alerce_sn));
  if (r.emailed) items.push(`<li class="muted">Email: ${esc(r.emailed)}</li>`);
  const title = r.state === "error" ? `Failed: ${esc(r.error)}` : esc(r.headline);
  return `<div class="report ${r.state}${r.read_at ? "" : " unread"}"><h3>${title} ${when}</h3><ul>${items.join("")}</ul></div>`;
}

function renderReports() {
  const reports = state.reports;
  if (!reports.length) {
    $("#reports").innerHTML = `<p class="muted">No reports yet. Pick the jobs below and "Run now", or turn on the daily run.</p>`;
    return;
  }
  const [latest, ...older] = reports;
  $("#reports").innerHTML = renderReport(latest) + (older.length
    ? `<details class="older"><summary class="muted">${older.length} earlier report${older.length > 1 ? "s" : ""}</summary>${older.map(renderReport).join("")}</details>`
    : "");
}

function renderSettings() {
  const s = state.settings, form = $("#settings");
  for (const el of form.elements) {
    if (!el.name || !(el.name in s)) continue;
    if (el.type === "checkbox") el.checked = !!s[el.name];
    else el.value = s[el.name];
  }
  const missing = s.filters.filter((f) => !state.saved_filters.includes(f));
  $("#filters").innerHTML = state.saved_filters.concat(missing).map((f) =>
    `<label><input type="checkbox" value="${esc(f)}" ${s.filters.includes(f) ? "checked" : ""}> ${esc(f)}${missing.includes(f) ? ' <span class="muted">(deleted)</span>' : ""}</label>`).join("")
    || `<span class="muted">No saved filters yet: save one in the <a href="/rejection">filter lab</a>.</span>`;
  $("#sn-list").textContent = state.sn_list;
  $("#sn-hint").textContent = `From ALeRCE's ${state.sn_classifier}, a stamp classifier; about 1,400 objects a night pass p ≥ 0.7 and a quarter of them have 2+ detections. ` +
    "The list's name says it was picked by a broker, which a blind label should keep in mind.";
  $("#email-hint").textContent = state.emails
    ? "Sent to your address in DOZYNKI_REPORT_EMAIL through the DOZYNKI_SMTP_* server, both set in .env."
    : `To email your reports, add ${state.labeler}=<address> to DOZYNKI_REPORT_EMAIL in .env (and set the DOZYNKI_SMTP_* server); the address is never stored in the database.`;
  $("#settings [name=email]").disabled = !state.emails;
}

function readSettings() {
  const form = $("#settings"), out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    out[el.name] = el.type === "checkbox" ? el.checked : el.type === "number" ? +el.value : el.value;
  }
  out.filters = [...document.querySelectorAll("#filters input:checked")].map((el) => el.value);
  return out;
}

async function load(first = false) {
  try {
    state = await api("/api/daily");
    $("#err").textContent = "";
  } catch (e) {
    $("#err").textContent = e.message;
    if (first) $("#jobs").hidden = $(".daily-controls").hidden = true;
    return;
  }
  renderReports();
  if (first) {
    renderSettings();
    $("#night").value = state.last_night;
    $("#night").max = state.last_night;
  }
  const running = state.reports.some((r) => r.state === "running");
  clearTimeout(poll);
  if (running) poll = setTimeout(load, 10000);
  else if (state.reports.some((r) => !r.read_at)) api("/api/daily/read", { method: "POST" }).catch(() => {});
}

$("#settings").onsubmit = async (e) => {
  e.preventDefault();
  try {
    state.settings = await api("/api/daily/settings", { method: "POST", body: readSettings() });
    $("#saved").textContent = "saved";
    toast(state.settings.enabled ? `Saved; the jobs run every day at ${state.settings.time}` : "Saved; the daily run is off");
  } catch (err) {
    toast("Save failed: " + err.message, 5000);
  }
};
$("#settings").oninput = () => ($("#saved").textContent = "unsaved changes");

$("#run-now").onclick = async () => {
  const night = $("#night").value;
  $("#run-now").disabled = true;
  try {
    await api("/api/daily/settings", { method: "POST", body: readSettings() });
    $("#saved").textContent = "saved";
    await api("/api/daily/run", { method: "POST", body: { night: night || null } });
    toast("Running the daily jobs");
    load();
  } catch (err) {
    toast("Couldn't run: " + err.message, 5000);
  } finally {
    $("#run-now").disabled = false;
  }
};

load(true);
