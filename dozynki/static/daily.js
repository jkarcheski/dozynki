// Daily page: your daily jobs' reports, newest first; when they run and whether they're emailed; and the jobs,
// added one at a time by picking a kind from JOBS.

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
  if (r.slacked) items.push(`<li class="muted">Slack: ${esc(r.slacked)}</li>`);
  const title = r.state === "error" ? `Failed: ${esc(r.error)}` : esc(r.headline);
  const failed = r.state === "error" || rep.night_error || [...(rep.filters || []), rep.watchlist, rep.alerce_sn].some((j) => j?.error);
  const rerun = failed ? `<button class="small" data-rerun="${r.id}">Rerun failed jobs</button>` : "";
  const post = state.slack && r.state === "done" ? `<button class="small" data-slack="${r.id}" title="Posts this report, with your DOZYNKI handle, to your Slack channel">Send to Slack</button>` : "";
  return `<div class="report ${r.state}${r.read_at ? "" : " unread"}"><h3>${title} ${when} ${rerun}${post}</h3><ul>${items.join("")}</ul></div>`;
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
  $("#reports").querySelectorAll("[data-slack]").forEach((b) => (b.onclick = async () => {
    b.disabled = true;
    try {
      await api(`/api/daily/slack/${b.dataset.slack}`, { method: "POST" });
      toast("Posted to Slack");
      load();
    } catch (err) {
      toast("Couldn't post to Slack: " + err.message, 5000);
      b.disabled = false;
    }
  }));
  $("#reports").querySelectorAll("[data-rerun]").forEach((b) => (b.onclick = async () => {
    b.disabled = true;
    try {
      await api(`/api/daily/rerun/${b.dataset.rerun}`, { method: "POST" });
      toast("Rerunning the failed jobs");
      load();
    } catch (err) {
      toast("Couldn't rerun: " + err.message, 5000);
      b.disabled = false;
    }
  }));
}

// The kinds of job, in the order "Add a job" offers them. Each says whether it is on in the settings, sums itself
// up in one line, draws its options and reads them back as the settings it sets; off is what removing it sets.
const JOBS = [
  {
    key: "filters",
    title: "Run last night through saved filters",
    blurb: "Like Filtered mode's whole-night run; the report counts what passes and is left to vet.",
    on: (s) => s.filters.length > 0,
    summary: (s) => s.filters.map(esc).join(", "),
    fields: (s) => {
      const missing = s.filters.filter((f) => !state.saved_filters.includes(f));
      return `<div class="checks">${state.saved_filters.concat(missing).map((f) =>
        `<label><input type="checkbox" name="filter" value="${esc(f)}" ${s.filters.includes(f) ? "checked" : ""}> ${esc(f)}${missing.includes(f) ? ' <span class="muted">(deleted)</span>' : ""}</label>`).join("")
        || `<span class="muted">No saved filters yet: save one in the <a href="/rejection">filter lab</a>.</span>`}</div>`;
    },
    read: (form) => {
      const filters = [...form.querySelectorAll("[name=filter]:checked")].map((el) => el.value);
      if (!filters.length) throw new Error("pick at least one filter");
      return { filters };
    },
    off: { filters: [] },
  },
  {
    key: "watchlist",
    title: "Recheck my watchlist",
    blurb: "Watched objects with new alerts go back to your queue.",
    on: (s) => s.watchlist,
    summary: () => "watched objects with new alerts go back to your queue",
    fields: () => `<p class="muted">Nothing to set: every object you're watching is rechecked in ANTARES.</p>`,
    read: () => ({ watchlist: true }),
    off: { watchlist: false },
  },
  {
    key: "alerce_sn",
    title: "ALeRCE's new supernovae",
    blurb: "New LSST objects whose top ALeRCE class is SN, added to an object list to vet.",
    on: (s) => s.alerce_sn,
    summary: (s) => `SN on top at p ≥ ${s.sn_min_prob}, ${s.sn_min_det}+ detections, first seen in the last ${s.sn_days} nights; ` +
      `up to ${s.sn_max} a day onto the list ${esc(state.sn_list)}`,
    fields: (s) => `<div class="row nums">
        <label>p ≥ <input type="number" name="sn_min_prob" min="0" max="1" step="0.05" value="${s.sn_min_prob}"></label>
        <label>detections ≥ <input type="number" name="sn_min_det" min="1" step="1" value="${s.sn_min_det}"></label>
        <label>first seen in the last <input type="number" name="sn_days" min="1" max="30" step="1" value="${s.sn_days}"> nights</label>
        <label>at most <input type="number" name="sn_max" min="1" max="2000" step="1" value="${s.sn_max}"> a day, most probable first</label>
      </div>
      <div class="hint">From ALeRCE's ${esc(state.sn_classifier)}, a stamp classifier; about 1,400 objects a night pass p ≥ 0.7 and
        a quarter of them have 2+ detections. The list ${esc(state.sn_list)} is picked by a broker, which a blind label should keep in mind.</div>`,
    read: (form) => Object.fromEntries(["sn_min_prob", "sn_min_det", "sn_days", "sn_max"]
      .map((k) => [k, +form.elements[k].value]).concat([["alerce_sn", true]])),
    off: { alerce_sn: false },
  },
];
let editing = null;  // the job whose options are open

async function save(change) {
  state.settings = await api("/api/daily/settings", { method: "POST", body: { ...state.settings, ...change } });
  renderJobs();
}

function renderSchedule() {
  const s = state.settings, form = $("#schedule");
  form.elements.enabled.checked = s.enabled;
  form.elements.time.value = s.time;
  form.elements.email.checked = s.email && state.emails;
  form.elements.email.disabled = !state.emails;
  form.elements.slack.checked = s.slack && state.slack;
  form.elements.slack.disabled = !state.slack;
  $("#slack-hint").textContent = (state.slack
    ? "to your channel's incoming webhook in DOZYNKI_SLACK_WEBHOOK (.env)"
    : `add ${state.labeler}=<incoming webhook URL> to DOZYNKI_SLACK_WEBHOOK in .env; the URL is never stored in the database`)
    + `. Everyone in the channel will see your DOZYNKI handle, ${state.labeler}: it's in the name of your ALeRCE SN list.`;
  $("#email-hint").textContent = state.emails
    ? "to your address in DOZYNKI_REPORT_EMAIL, through the DOZYNKI_SMTP_* server (.env)"
    : `add ${state.labeler}=<address> to DOZYNKI_REPORT_EMAIL in .env, and set the DOZYNKI_SMTP_* server; the address is never stored in the database`;
}

function renderJobs() {
  const s = state.settings, on = JOBS.filter((j) => j.on(s)), left = JOBS.filter((j) => !j.on(s));
  $("#job-list").innerHTML = on.map((j) => `<div class="job"><div><b>${esc(j.title)}</b><div class="muted">${j.summary(s)}</div></div>
      <span class="job-actions"><button type="button" class="small" data-edit="${j.key}">Edit</button>
      <button type="button" class="small" data-remove="${j.key}">Remove</button></span></div>`).join("")
    || `<p class="muted">No jobs yet: add one, and the report says what's new each morning.</p>`;
  $("#job-list").querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => edit(b.dataset.edit)));
  $("#job-list").querySelectorAll("[data-remove]").forEach((b) => (b.onclick = async () => {
    try { await save(JOBS.find((j) => j.key === b.dataset.remove).off); toast("Job removed"); }
    catch (err) { toast("Couldn't remove: " + err.message, 5000); }
  }));
  $("#job-kinds").innerHTML = left.map((j) => `<button type="button" class="choice" data-kind="${j.key}">
      <span class="c-label">${esc(j.title)}</span><span class="impact">${esc(j.blurb)}</span></button>`).join("");
  $("#job-kinds").querySelectorAll("[data-kind]").forEach((b) => (b.onclick = () => edit(b.dataset.kind)));
  $("#add-job").hidden = !left.length || !$("#job-pick").hidden || !$("#job-edit").hidden;
}

function edit(key) {
  editing = JOBS.find((j) => j.key === key);
  $("#job-pick").hidden = true;
  $("#job-title").textContent = editing.title;
  $("#job-fields").innerHTML = editing.fields(state.settings);
  $("#job-edit").hidden = false;
  renderJobs();
}

function closeEditors() {
  editing = null;
  $("#job-pick").hidden = $("#job-edit").hidden = true;
  renderJobs();
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
    renderSchedule();
    renderJobs();
    $("#night").value = state.last_night;
    $("#night").max = state.last_night;
  }
  const running = state.reports.some((r) => r.state === "running");
  clearTimeout(poll);
  if (running) poll = setTimeout(load, 10000);
  else if (state.reports.some((r) => !r.read_at)) api("/api/daily/read", { method: "POST" }).catch(() => {});
}

$("#schedule").onsubmit = async (e) => {
  e.preventDefault();
  const form = e.target;
  try {
    await save({ enabled: form.elements.enabled.checked, time: form.elements.time.value,
                 email: state.emails ? form.elements.email.checked : state.settings.email,
                 slack: state.slack ? form.elements.slack.checked : state.settings.slack });
    $("#saved").textContent = "saved";
    toast(state.settings.enabled ? `Saved; the jobs run every day at ${state.settings.time}` : "Saved; the daily run is off");
  } catch (err) {
    toast("Save failed: " + err.message, 5000);
  }
};
$("#schedule").oninput = () => ($("#saved").textContent = "unsaved changes");

$("#add-job").onclick = () => {
  $("#job-pick").hidden = false;
  renderJobs();
};
$("#pick-cancel").onclick = $("#edit-cancel").onclick = closeEditors;
$("#job-edit").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await save(editing.read(e.target));
    toast(`Saved: ${editing.title}`);
    closeEditors();
  } catch (err) {
    toast("Couldn't save the job: " + err.message, 5000);
  }
};

$("#run-now").onclick = async () => {
  const night = $("#night").value;
  $("#run-now").disabled = true;
  try {
    await api("/api/daily/run", { method: "POST", body: { night: night || null } });
    toast("Running the daily jobs");
    load();
  } catch (err) {
    toast("Couldn't run: " + err.message, 5000);
  } finally {
    $("#run-now").disabled = false;
  }
};

window.addEventListener("daily:changed", () => load());  // the pop-up started or finished a rerun
// the tab bar's search box: mark the object wherever a report names it
window.onFind = (hit) => (Find.mark(hit.object_id) ? null : "isn't in the daily reports shown here");
load(true);
