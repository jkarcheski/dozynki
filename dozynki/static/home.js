// Landing page: labeling progress from /api/stats, the wheat field, and links to the other pages.

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
  return r.json();
}

function renderLeaderboard(lb, me) {
  const top = Math.max(1, ...lb.labelers.map((r) => r.objects));
  $("#leaderboard").innerHTML =
    `<tr><th class="num">#</th><th>Labeler</th><th class="num">Objects</th><th></th><th class="num">Blind</th>`
    + `<th class="num">Last ${lb.days} days</th><th>Last label</th></tr>`
    + lb.labelers.map((r, i) => `<tr${r.labeler === me || (me === "guest" && r.labeler === "guests") ? ' class="me"' : ""}><td class="num">${i + 1}</td>`
      + `<td>${esc(r.labeler)}</td><td class="num">${r.objects}</td>`
      + `<td class="lb-bar"><i style="width:${(100 * r.objects / top).toFixed(1)}%"></i></td>`
      + `<td class="num">${r.blind}</td><td class="num">${r.recent}</td>`
      + `<td class="muted">${esc((r.last_at || "").slice(0, 10))}</td></tr>`).join("")
    || `<tr><td class="muted">No labels yet.</td></tr>`;
}

async function init() {
  const [cfg, s, lb] = await Promise.all([api("/api/config"), api("/api/stats"), api("/api/leaderboard")]);
  renderLeaderboard(lb, cfg.labeler);
  const q = s.queue;
  Harvest.field($("#harvest"), q.pending || 0);
  $("#blind").textContent = cfg.blind ? "blind mode on" : "blind mode off";

  const tiles = [
    ["pending", q.pending], ["labeled", q.labeled], ["watching", q.watch], ["skipped", q.skipped],
    ["changed on a later look", s.revised_after_reveal],
  ];
  $("#tiles").innerHTML = tiles.map(([k, n]) => `<div class="tile"><b>${n || 0}</b><span>${k}</span></div>`).join("");
  if (q.pending) $("#vet-cta").textContent = `Continue labeling (${q.pending} queued) →`;

  // Classes in taxonomy order, colored like the sky map; latest label per object.
  const known = cfg.classes.map((c) => c.key);
  const rows = cfg.classes.filter((c) => s.by_class[c.key])
    .map((c) => ({ key: c.key, label: c.label, n: s.by_class[c.key] }))
    .concat(Object.entries(s.by_class).filter(([k]) => !known.includes(k)).map(([k, n]) => ({ key: "none", label: k, n })));
  const total = rows.reduce((a, r) => a + r.n, 0);
  if (!total) {
    $("#class-legend").textContent = "No labels yet.";
    return;
  }
  $("#class-bar").innerHTML = rows.map((r) =>
    `<i style="--c:var(--cls-${r.key});flex:${r.n}" title="${esc(r.label)}: ${r.n} (${(100 * r.n / total).toFixed(0)}%)"></i>`).join("");
  $("#class-legend").innerHTML = rows.map((r) =>
    `<span style="--c:var(--cls-${r.key})"><i></i>${esc(r.label)} <b>${r.n}</b></span>`).join("");
}

init().catch((e) => {
  $("#err").textContent = e.message;
  if (!$("#harvest").innerHTML) Harvest.field($("#harvest"));
});
