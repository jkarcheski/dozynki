// Landing page: labeling progress from /api/stats, the farm shop, and links to the other pages.

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
  return r.json();
}

async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail || `${r.status}`);
  return r.json();
}

// Farm: coins from /api/farm (farm.py), owned items drawn into the wheat field.
let pending = 0;

function renderFarm(f) {
  const c = f.coins;
  const owned = [...f.shop, ...f.unlocks].filter((i) => i.owned).map((i) => i.key);
  Harvest.field($("#harvest"), pending, owned, f.wearing);
  $("#coins").innerHTML = `<b>${c.balance}</b> coin${c.balance === 1 ? "" : "s"}`;
  $("#coin-why").textContent = `Earned ${c.total}: ${c.label} for objects vetted, ${c.host} for hosts chosen, `
    + `${c.extra} for flags or notes (one per object)${c.spent ? `; spent ${c.spent}` : ""}.`;
  $("#shop").innerHTML = f.shop.map((i) => `<div class="item${i.owned ? " owned" : ""}">${Harvest.farmIcon(i.key)}`
    + `<b>${esc(i.name)}</b>`
    + (i.owned ? `<span class="muted">on your farm</span>`
      : `<button class="small${c.balance >= i.price ? " primary" : ""}" data-k="${esc(i.key)}"${c.balance < i.price ? " disabled" : ""}>`
        + `${i.price} coins</button>`) + `</div>`).join("");
  $("#unlocks").innerHTML = f.unlocks.map((i) => `<div class="item${i.owned ? " owned" : " locked"}" title="${esc(i.what)}">`
    + `${Harvest.farmIcon(i.key)}<b>${esc(i.name)}</b>`
    + `<span class="muted">${i.owned ? "on your farm" : `earn ${esc(i.achievement)}`}</span></div>`).join("");
  Harvest.fitIcons($("#shop"));
  Harvest.fitIcons($("#unlocks"));
  $("#shop").querySelectorAll("button[data-k]").forEach((b) => (b.onclick = () =>
    act(b, "#shop-err", "/api/farm/buy", { item: b.dataset.k })));
  renderWardrobe(f);
}

async function act(button, errSel, path, body) {
  button.disabled = true;
  try {
    $(errSel).textContent = "";
    renderFarm(await post(path, body));
  } catch (e) {
    $(errSel).textContent = e.message;
    button.disabled = false;
  }
}

const SLOT_NAMES = { hat: "Hats", hair: "Hairstyles", outfit: "Outfits", boots: "Boots" };

// Each wardrobe item is shown on the farmer as they are now dressed, so you see how it would look.
function renderWardrobe(f) {
  const c = f.coins;
  $("#farmer-preview").innerHTML = Harvest.farmerIcon(f.wearing, "farmer-big");
  $("#wardrobe").innerHTML = f.slots.map((slot) => `<h3>${esc(SLOT_NAMES[slot] || slot)}</h3><div class="shop">`
    + f.wardrobe.filter((i) => i.slot === slot).map((i) => {
      const button = i.worn ? `<button class="small" data-slot="${slot}">Take off</button>`
        : i.owned ? `<button class="small" data-slot="${slot}" data-wear="${esc(i.key)}">Wear</button>`
        : `<button class="small${c.balance >= i.price ? " primary" : ""}" data-buy="${esc(i.key)}"${c.balance < i.price ? " disabled" : ""}>`
          + `${i.price} coins</button>`;
      return `<div class="item${i.worn ? " owned" : ""}">${Harvest.farmerIcon({ ...f.wearing, [slot]: i.key }, "farm-icon", slot)}`
        + `<b>${esc(i.name)}</b>${button}</div>`;
    }).join("") + `</div>`).join("");
  $("#wardrobe").querySelectorAll("button").forEach((b) => (b.onclick = () => b.dataset.buy
    ? act(b, "#wardrobe-err", "/api/farm/buy", { item: b.dataset.buy })
    : act(b, "#wardrobe-err", "/api/farm/wear", { slot: b.dataset.slot, item: b.dataset.wear || null })));
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
  const dflt = rc.default_pipeline.map(({ rule, ...params }) => ({ rule, on: true, params }));
  return Number(norm(saved.pipeline || saved.A) !== norm(dflt));
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
  $("#tns-at").textContent = ach.tns_checked_at ? `TNS checked ${ach.tns_checked_at.slice(0, 16).replace("T", " ")} UTC` : "TNS not checked yet";
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

async function checkTns(rc) {
  const btn = $("#tns-check");
  btn.disabled = true;
  btn.textContent = "Checking…";
  try {
    const r = await post("/api/tns/check", {});
    const failed = Object.keys(r.errors).length;
    renderAchievements(await api("/api/achievements"), rc);
    $("#tns-at").textContent = `${r.matched} of ${r.checked} transients on TNS` + (failed ? `, ${failed} failed` : "");
  } catch (e) {
    $("#err").textContent = e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "Check TNS";
  }
}

// A whole night run through a saved filter (filtered mode): how many pass, and how far you are.
function runRow(r) {
  const n = (x) => (x ?? 0).toLocaleString();
  const pass = r.screening ? `${n(r.kept)} to ${n(r.kept + r.screening)}` : n(r.kept);
  const status = r.state === "error" ? `failed: ${esc(r.error)}` : r.state !== "done" ? "going through ANTARES…"
    : r.done ? "finished" : r.screening ? `checking ${n(r.screening)}; ${n(r.left)} left to vet` : `${n(r.left)} left to vet`;
  return `<tr><td>${esc(r.night)} <span class="pill" title="Filtered mode: the whole night through this saved filter">${esc(r.screen)}</span></td>`
    + `<td class="num muted">whole night</td><td class="num" title="first or latest alert that night; passing every rule">${pass} of ${n(r.n_night)} pass</td>`
    + `<td class="num">${n(r.vetted)}${r.filtered ? ` <span class="muted" title="thrown out by the rules that need each object fetched">+ ${n(r.filtered)} filtered</span>` : ""}</td>`
    + `<td class="lb-bar" title="vetted of those that passed"><i style="width:${(100 * r.vetted / Math.max(1, r.kept + r.screening)).toFixed(1)}%"></i></td>`
    + `<td class="muted">${status}</td></tr>`;
}

function renderNights(rows, runs = []) {
  if (!rows.length && !runs.length) {
    $("#nights").innerHTML = `<tr><td class="muted">No nights yet. Pick a night next to "Sample night" in the vetter, or "Filter whole night" in filtered mode.</td></tr>`;
    return;
  }
  const size = (r) => (r.capped ? "10000+" : r.n_matching);
  $("#nights").innerHTML =
    `<tr><th>Night</th><th class="num">Detections</th><th class="num">Sampled</th><th class="num">Vetted</th><th></th><th></th></tr>`
    + rows.map((r) => `<tr><td>${esc(r.night)}${r.screen ? ` <span class="pill" title="Filtered mode: screened with this saved filter">${esc(r.screen)}</span>` : ""}</td>`
      + `<td class="num">≥ ${r.min_det}</td><td class="num">${r.taken} of ${size(r)}</td>`
      + `<td class="num">${r.vetted}${r.filtered ? ` <span class="muted" title="thrown away by the filter">+ ${r.filtered} filtered</span>` : ""}</td>`
      + `<td class="lb-bar" title="vetted (and filtered out) of the whole night"><i style="width:${(100 * (r.vetted + r.filtered) / Math.max(1, r.n_matching)).toFixed(1)}%"></i></td>`
      + `<td class="muted">${r.done ? "finished" : r.capped ? "too many to finish; raise the detection minimum"
        : r.taken < r.n_matching ? `${r.n_matching - r.taken} left to sample`
        : r.screening ? `screening ${r.screening}` : `${r.taken - r.vetted - r.filtered} left to vet`}</td></tr>`).join("")
    + runs.map(runRow).join("");
}

async function init() {
  const [cfg, s, lb, ach, rc, farm] = await Promise.all([api("/api/config"), api("/api/stats"), api("/api/leaderboard"),
    api("/api/achievements"), api("/api/rejection/config"), api("/api/farm")]);
  api("/api/nights").then((n) => renderNights(n.nights, n.runs)).catch((e) => { $("#err").textContent = e.message; });
  renderLeaderboard(lb, cfg.labeler);
  renderAchievements(ach, rc);
  $("#tns-check").onclick = () => checkTns(rc);
  const q = s.queue;
  pending = q.pending || 0;
  renderFarm(farm);
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
