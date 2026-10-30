// Watchlist: your objects flagged "watch", filtered by when they were flagged, with a CSV download.

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let CLASSES = {};

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
  const q = since ? `?since=${since}` : "";
  $("#csv").href = `/api/watchlist.csv${q}`;
  try {
    const { objects } = await api(`/api/watchlist${q}`);
    $("#err").textContent = "";
    $("#count").textContent = `· ${objects.length} object${objects.length === 1 ? "" : "s"}`;
    $("#list").innerHTML = objects.length
      ? `<tr><th>Object</th><th>Class</th><th>Confidence</th><th>Flags</th><th>Notes</th><th class="num">RA</th><th class="num">Dec</th><th>Flagged (UTC)</th></tr>`
        + objects.map((o) => `<tr>`
          + `<td><a href="/vet?object=${encodeURIComponent(o.object_id)}">${esc(o.object_id)}</a></td>`
          + `<td>${esc(CLASSES[o.klass] ?? o.klass)}${o.subclass ? ` <span class="muted">${esc(o.subclass)}</span>` : ""}</td>`
          + `<td>${esc(o.confidence)}</td>`
          + `<td>${JSON.parse(o.flags || "[]").filter((f) => f !== "watch").map((f) => `<span class="pill">${esc(f)}</span>`).join("")}</td>`
          + `<td>${esc(o.notes)}</td>`
          + `<td class="num">${o.ra?.toFixed(5) ?? "—"}</td><td class="num">${o.dec?.toFixed(5) ?? "—"}</td>`
          + `<td class="muted">${esc(o.flagged_at.slice(0, 16).replace("T", " "))}</td></tr>`).join("")
      : `<tr><td class="muted">Nothing on your watchlist${since ? " flagged since " + since : ""}.</td></tr>`;
  } catch (e) {
    $("#err").textContent = e.message;
  }
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
  $("#recheck").onclick = recheck;
  setRange("night");
}

init().catch((e) => ($("#err").textContent = e.message));
