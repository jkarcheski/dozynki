// Landing page: labeling progress from /api/stats and links to the other pages.

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
  return r.json();
}

async function init() {
  const [cfg, s] = await Promise.all([api("/api/config"), api("/api/stats")]);
  const q = s.queue;
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

init().catch((e) => { $("#err").textContent = e.message; });
