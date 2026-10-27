// Landing page: labeling progress from /api/stats and links to the other pages.

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
    + lb.labelers.map((r, i) => `<tr${r.labeler === me ? ' class="me"' : ""}><td class="num">${i + 1}</td>`
      + `<td>${esc(r.labeler)}</td><td class="num">${r.objects}</td>`
      + `<td class="lb-bar"><i style="width:${(100 * r.objects / top).toFixed(1)}%"></i></td>`
      + `<td class="num">${r.blind}</td><td class="num">${r.recent}</td>`
      + `<td class="muted">${esc((r.last_at || "").slice(0, 10))}</td></tr>`).join("")
    || `<tr><td class="muted">No labels yet.</td></tr>`;
}

// The filter lab keeps its pipelines in the browser (rejection.js), so filter
// achievements are checked here: a pipeline counts once it differs from its default.
const FILTERLAB_KEY = "dozynki.filterlab.v1";

function filtersMade(rc) {
  let saved;
  try { saved = JSON.parse(localStorage.getItem(FILTERLAB_KEY)); } catch { return 0; }
  if (!saved) return 0;
  const byName = Object.fromEntries(rc.rules.map((r) => [r.name, r]));
  const norm = (steps) => JSON.stringify((steps || []).filter((s) => byName[s.rule] && s.on)
    .map((s) => [s.rule, { ...byName[s.rule].defaults, ...s.params }]));
  const A = rc.default_pipeline.map(({ rule, ...params }) => ({ rule, on: true, params }));
  const B = A.filter((s) => s.rule !== "min_nights"); // rejection.js defaultState()
  return (norm(saved.A) !== norm(A)) + (norm(saved.B) !== norm(B));
}

function renderAchievements(ach, rc) {
  for (const a of ach.achievements) {
    if (a.category !== "filtering") continue;
    a.count = filtersMade(rc);
    a.earned = a.tiers.filter((t) => a.count >= t).length;
    a.next = a.tiers[a.earned] ?? null;
  }
  const tiers = ach.achievements.reduce((n, a) => n + a.tiers.length, 0);
  const won = ach.achievements.reduce((n, a) => n + a.earned, 0);
  $("#ach-count").textContent = `${won} of ${tiers} tiers`;
  $("#achievements").innerHTML = ach.categories.map((cat) => `<h3>${esc(cat)}</h3><div class="badges">`
    + ach.achievements.filter((a) => a.category === cat).map((a) => {
      const goal = a.next ?? a.tiers[a.tiers.length - 1];
      const pips = a.tiers.map((t, i) => `<i class="${i < a.earned ? "on" : ""}" title="${t}"></i>`).join("");
      return `<div class="badge${a.earned ? " won" : ""}${a.next === null ? " done" : ""}">`
        + `<b>${esc(a.name)}</b><span class="pips">${pips}</span>`
        + `<span class="what">${esc(a.what)}</span>`
        + `<span class="prog"><i style="width:${Math.min(100, 100 * a.count / goal).toFixed(1)}%"></i></span>`
        + `<span class="muted">${a.count} / ${a.next === null ? "all tiers" : a.next}</span></div>`;
    }).join("") + `</div>`).join("");
}

function renderNights(rows) {
  if (!rows.length) {
    $("#nights").innerHTML = `<tr><td class="muted">No nights sampled yet. Pick a night next to "Sample night" in the vetter.</td></tr>`;
    return;
  }
  const size = (r) => (r.capped ? "10000+" : r.n_matching);
  $("#nights").innerHTML =
    `<tr><th>Night</th><th class="num">Detections</th><th class="num">Sampled</th><th class="num">Vetted</th><th></th><th></th></tr>`
    + rows.map((r) => `<tr><td>${esc(r.night)}</td><td class="num">≥ ${r.min_det}</td>`
      + `<td class="num">${r.taken} of ${size(r)}</td><td class="num">${r.vetted}</td>`
      + `<td class="lb-bar" title="vetted of the whole night"><i style="width:${(100 * r.vetted / Math.max(1, r.n_matching)).toFixed(1)}%"></i></td>`
      + `<td class="muted">${r.done ? "finished" : r.capped ? "too many to finish; raise the detection minimum"
        : r.taken < r.n_matching ? `${r.n_matching - r.taken} left to sample` : `${r.taken - r.vetted} left to vet`}</td></tr>`).join("");
}

async function init() {
  const [cfg, s, lb, ach, rc] = await Promise.all([api("/api/config"), api("/api/stats"), api("/api/leaderboard"),
    api("/api/achievements"), api("/api/rejection/config")]);
  api("/api/nights").then((n) => renderNights(n.nights)).catch((e) => { $("#err").textContent = e.message; });
  renderLeaderboard(lb, cfg.labeler);
  renderAchievements(ach, rc);
  const q = s.queue;
  Harvest.field($("#harvest"), q.pending || 0);
  if (cfg.labeler) $("#labeler").innerHTML = `labeling as <b>${esc(cfg.labeler)}</b>`;
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
