// Landing page Coverage card: share of LSST objects vetted, overall and per night, from /api/coverage (coverage.py).
// A month calendar with one circle per Rubin night, shaded by the share of that night's objects vetted.

(() => {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = (n) => n.toLocaleString("en-US");
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

  // Shares are tiny next to a whole night, so the steps are logarithmic; one hue, light to dark.
  const STEPS = [
    { max: 0.001, label: "< 0.1%" },
    { max: 0.01, label: "0.1–1%" },
    { max: 0.1, label: "1–10%" },
    { max: 1, label: "10–99%" },
    { max: Infinity, label: "all" },
  ];
  const step = (share) => (share >= 1 ? 4 : STEPS.findIndex((s) => share < s.max));

  function pct(k, n) {
    if (!n) return "—";
    const p = (100 * k) / n;
    return p === 0 ? "0%" : p < 0.01 ? "< 0.01%" : p < 1 ? `${p.toFixed(2)}%` : `${p.toFixed(1)}%`;
  }

  function dot(r) {
    let cls = "cv-dot";
    if (r.size === null) cls += " uncounted";
    else if (r.size === 0) cls += " empty";
    else if (r.vetted) cls += ` s${step(r.vetted / r.size)}`;
    if (r.mine) cls += " mine";
    const label = r.size === null ? `${r.night}: not counted yet` + (r.vetted ? `; ${r.vetted} vetted` : "")
      : r.size === 0 ? `${r.night}: no LSST objects first alerted`
      : `${r.night}: ${pct(r.vetted, r.size)} of ${fmt(r.size)} objects vetted (${fmt(r.vetted)}; you ${fmt(r.mine)})`;
    return `<span class="${cls}" data-tip="${esc(label)}" aria-label="${esc(label)}" role="img"></span>`;
  }

  function month(key, rows) {
    const [y, m] = key.split("-").map(Number);
    const lead = (new Date(Date.UTC(y, m - 1, 1)).getUTCDay() + 6) % 7; // Monday first
    const byDay = Object.fromEntries(rows.map((r) => [Number(r.night.slice(8)), r]));
    const days = new Date(Date.UTC(y, m, 0)).getUTCDate();
    const cells = Array.from({ length: lead }, () => "<i></i>")
      .concat(Array.from({ length: days }, (_, i) => (byDay[i + 1] ? dot(byDay[i + 1]) : "<i></i>")));
    const sum = rows.reduce((a, r) => ({ size: a.size + (r.size || 0), vetted: a.vetted + (r.size !== null ? r.vetted : 0) }), { size: 0, vetted: 0 });
    return `<div class="cv-month"><div class="cv-mhead"><b>${MONTHS[m - 1].slice(0, 3)} ${y}</b>`
      + `<span class="hint">${sum.size ? pct(sum.vetted, sum.size) : ""}</span></div>`
      + `<div class="cv-grid">${["M", "T", "W", "T", "F", "S", "S"].map((d) => `<em>${d}</em>`).join("")}${cells.join("")}</div></div>`;
  }

  function legend() {
    return `<span><i class="cv-dot"></i> none vetted</span>`
      + STEPS.map((s, i) => `<span><i class="cv-dot s${i}"></i> ${s.label}</span>`).join("")
      + `<span><i class="cv-dot mine"></i> you vetted here</span>`
      + `<span><i class="cv-dot empty"></i> no objects</span><span><i class="cv-dot uncounted"></i> not counted yet</span>`;
  }

  function table(nights) {
    const rows = nights.filter((r) => r.vetted || r.size);
    return `<tr><th>Night</th><th class="num">Objects</th><th class="num">Vetted</th><th class="num">Share</th><th class="num">You</th></tr>`
      + rows.reverse().map((r) => `<tr><td>${r.night}</td><td class="num">${r.size === null ? "—" : fmt(r.size)}</td>`
        + `<td class="num">${fmt(r.vetted)}</td><td class="num">${pct(r.vetted, r.size)}</td><td class="num">${fmt(r.mine)}</td></tr>`).join("");
  }

  function render(c) {
    const t = c.total;
    $("#cv-hero").innerHTML = t.size
      ? `<b>${pct(t.vetted, t.size)}</b><span>of ${fmt(t.size)} LSST objects vetted: ${fmt(t.vetted)} by everyone, `
        + `<strong>${fmt(t.mine)} (${pct(t.mine, t.size)}) by you</strong></span>`
      : `<b>—</b><span>no night has been counted yet</span>`;
    const notes = [];
    if (t.nights_counted < t.nights) notes.push(`${t.nights_counted} of ${t.nights} nights counted${c.counting ? ", counting…" : ""}`);
    if (c.error) notes.push(`ANTARES didn't answer (${esc(c.error.slice(0, 120))}); trying again in a few minutes`);
    if (c.unknown_night.vetted) notes.push(`${fmt(c.unknown_night.vetted)} vetted objects don't know their night yet (they'll be looked up)`);
    $("#cv-note").innerHTML = notes.join(" · ");
    const months = {};
    for (const r of c.nights) (months[r.night.slice(0, 7)] ||= []).push(r);
    $("#cv-cal").innerHTML = Object.entries(months).map(([k, rows]) => month(k, rows)).join("");
    $("#cv-legend").innerHTML = legend();
    $("#cv-table").innerHTML = table(c.nights);
    return c.counting || (t.nights_counted < t.nights && !c.error);
  }

  // One floating tooltip for every circle.
  function tooltips() {
    const tip = $("#cv-tip");
    $("#cv-cal").addEventListener("pointerover", (e) => {
      const d = e.target.closest("[data-tip]");
      if (!d) return;
      tip.textContent = d.dataset.tip;
      const r = d.getBoundingClientRect();
      tip.hidden = false;
      tip.style.left = `${Math.min(window.innerWidth - tip.offsetWidth - 8, Math.max(8, r.left + r.width / 2 - tip.offsetWidth / 2))}px`;
      tip.style.top = `${r.top - tip.offsetHeight - 6}px`;
    });
    $("#cv-cal").addEventListener("pointerout", () => { tip.hidden = true; });
  }

  async function load() {
    try {
      const r = await fetch("/api/coverage");
      if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
      if (render(await r.json())) setTimeout(load, 15000); // pick up nights as they're counted
    } catch (e) {
      $("#cv-note").textContent = e.message;
    }
  }

  tooltips();
  load();
})();
