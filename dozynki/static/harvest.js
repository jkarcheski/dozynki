// Harvest-festival decorations: the wheat field on the landing page with the farm built in it, the haystack
// in the vetter header (pending objects of the latest batch), the harvest played when
// a batch is sampled, and the needle when a commit lands on a known transient.
// Plain SVG + CSS (harvest.css); everything lives in this closure so it can sit next
// to app.js and home.js, which declare their own top-level $ and esc.

const Harvest = (() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const calm = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const STACK_KEY = "dozynki.haystack.v1"; // { full }: pending count right after the latest batch

  // Seeded so the field looks the same on every load.
  function rng(seed) {
    return () => {
      seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // One wheat stalk with its base at the origin; cls picks the animation (sway or cut).
  function stalk(x, y, h, bend, delay, cls = "sway") {
    let head = "";
    for (let k = 0; k < 6; k++) {
      const gy = -h + 4 + k * 4.2, side = k % 2 ? 1 : -1;
      head += `<ellipse cx="${side * 2.1}" cy="${gy}" rx="2" ry="3.6" transform="rotate(${side * 28} ${side * 2.1} ${gy})"/>`;
    }
    head += `<ellipse cx="0" cy="${-h + 1}" rx="1.8" ry="3.4"/>`;
    const awns = [-6, -2, 2, 6].map((dx) => `<path d="M0 ${-h} l${dx} -9"/>`).join("");
    return `<g transform="translate(${x.toFixed(1)} ${y})"><g class="${cls}" style="--d:${delay.toFixed(2)}s">`
      + `<path class="stem" d="M0 0 Q${bend.toFixed(1)} ${-h / 2} 0 ${-h + 6}"/>`
      + `<path class="stem" d="M0 ${(-h * 0.35).toFixed(1)} q${bend > 0 ? 7 : -7} -4 ${bend > 0 ? 10 : -10} -13"/>`
      + `<g class="awns">${awns}</g><g class="grain">${head}</g></g></g>`;
  }

  function stalks(rand, x0, x1, y, n, hMin, hMax, delayOf) {
    let out = "";
    for (let i = 0; i < n; i++) {
      const x = x0 + (x1 - x0) * (i + rand()) / n;
      out += stalk(x, y + rand() * 4, hMin + rand() * (hMax - hMin), (rand() - 0.5) * 10, delayOf(x, rand));
    }
    return out;
  }

  // Dome of hay with its base center at the origin.
  function haystack(rand = rng(7)) {
    let straw = "";
    for (let i = 0; i < 26; i++) {
      const a = rand() * Math.PI, r = 8 + rand() * 38;
      const x = Math.cos(a) * r, y = -Math.sin(a) * r * 1.35 - 4, t = (rand() - 0.5) * 1.4;
      straw += `<path d="M${x.toFixed(1)} ${y.toFixed(1)} l${(Math.cos(t) * 9).toFixed(1)} ${(Math.sin(t) * 9).toFixed(1)}"/>`;
    }
    const tufts = [-14, -5, 4, 12].map((x) => `<path d="M${x} -66 l${(x / 3).toFixed(1)} -9"/>`).join("");
    return `<path class="dome" d="M-50 0 C-48 -40 -28 -70 0 -72 C28 -70 48 -40 50 0 Z"/><g class="straw">${straw}${tufts}</g>`;
  }

  function tractor() {
    const spokes = (r) => `<path d="M${-r} 0 H${r} M0 ${-r} V${r} M${-r * .7} ${-r * .7} L${r * .7} ${r * .7} M${-r * .7} ${r * .7} L${r * .7} ${-r * .7}"/>`;
    return `<g class="wagon">
        <path class="dome" d="M-74 -22 C-72 -38 -60 -46 -50 -46 C-40 -46 -28 -38 -26 -22 Z"/>
        <rect x="-76" y="-24" width="52" height="12" rx="2"/><path class="hitch" d="M-24 -16 H-12"/>
        <g transform="translate(-50 -6)"><g class="wheel"><circle r="7"/>${spokes(6)}</g></g>
      </g>
      <g class="tractor-body">
        <path class="puff" d="M24 -44 a4 4 0 1 1 0.1 0"/><path class="puff p2" d="M24 -44 a4 4 0 1 1 0.1 0"/>
        <rect class="pipe" x="22" y="-42" width="3" height="14"/>
        <rect class="paint" x="-12" y="-28" width="44" height="16" rx="3"/>
        <rect class="paint" x="-12" y="-48" width="20" height="22" rx="2"/>
        <rect class="glass" x="-8" y="-45" width="12" height="11" rx="1"/>
        <g transform="translate(-2 -14)"><g class="wheel big"><circle r="14"/>${spokes(12)}<circle class="hub" r="4"/></g></g>
        <g transform="translate(26 -8)"><g class="wheel"><circle r="8"/>${spokes(7)}<circle class="hub" r="2.5"/></g></g>
      </g>`;
  }

  // ---------- farm (achievement unlocks, farm.py) ----------

  // Each item with its base center at the origin; x is its spot in the 1000-wide field, w the
  // width of front wheat it clears so it shows.
  const FARM = {
    weathervane: [135, 30, () => `<path class="spar" d="M0 0 V-96"/><path class="vane" d="M-16 -88 H14 M14 -88 l-6 -4 M14 -88 l-6 4"/>`
      + `<path class="vane-tail" d="M-16 -88 l-6 -6 v12 Z"/><path class="vane" d="M-6 -78 H6 M0 -84 V-72"/>`],
    stork: [290, 40, () => `<path class="spar" d="M0 0 V-90"/><ellipse class="nest" cx="0" cy="-92" rx="16" ry="5"/>`
      + `<ellipse class="bird" cx="1" cy="-102" rx="10" ry="6"/><path class="bird-wing" d="M-8 -102 Q2 -96 10 -101 Q2 -104 -8 -102 Z"/>`
      + `<path class="neck" d="M8 -105 Q12 -116 10 -122"/><circle class="bird" cx="10" cy="-123" r="3"/>`
      + `<path class="beak" d="M12 -123 l9 3"/>`],
    rocks: [338, 46, () => `<ellipse class="rock" cx="-11" cy="-5" rx="11" ry="7"/><ellipse class="rock" cx="9" cy="-4" rx="9" ry="5"/>`
      + `<ellipse class="rock" cx="-1" cy="-10" rx="7" ry="6"/>`],
    lantern: [800, 30, () => `<path class="spar" d="M0 0 V-80 H14"/><path class="rope" d="M12 -80 V-72"/>`
      + `<circle class="glow" cx="12" cy="-62" r="12"/><rect class="lamp" x="7" y="-72" width="10" height="16" rx="2"/>`
      + `<path class="trim" d="M7 -64 H17"/>`],
    telescope: [838, 40, () => `<path class="leg" d="M0 -30 L-12 0 M0 -30 L12 0 M0 -30 V0"/>`
      + `<g transform="translate(0 -32) rotate(-38)"><rect class="tube" x="-16" y="-5" width="36" height="10" rx="2"/>`
      + `<rect class="tube-end" x="18" y="-6" width="5" height="12" rx="1"/></g>`],
  };

  // A small picture of one unlock for the landing page's list, framed on a strip of ground.
  function farmIcon(key) {
    const [, w, draw] = FARM[key];
    const half = Math.max(30, w / 2 + 6);
    return `<svg class="farm-icon" viewBox="${-half} -128 ${2 * half} 134" aria-hidden="true">`
      + `<path class="ground" d="M${-half} 0 H${half} V6 H${-half} Z"/><g class="farm-item ${key}">${draw()}</g></svg>`;
  }

  // Crop farm icons to what they draw, once they are on the page (short items would sit low in a tall frame).
  function fitIcons(root) {
    root.querySelectorAll(".farm-icon").forEach((svg) => {
      const b = svg.getBBox(), pad = 4, w = Math.max(b.width, b.height) + 2 * pad;
      svg.setAttribute("viewBox", `${(b.x + b.width / 2 - w / 2).toFixed(1)} ${(b.y + b.height - w + pad).toFixed(1)} ${w.toFixed(1)} ${w.toFixed(1)}`);
    });
  }

  // ---------- farmer ----------

  // Standing with the feet at the origin, about 66 tall: short hair, work clothes, shoes, no hat.
  function farmer() {
    return `<g class="farmer-figure"><path class="trousers" d="M-7 -27 H7 L6 -3 H1.6 L0 -20 L-1.6 -3 H-6 Z"/>`
      + `<path class="shoe" d="M-6.2 -3 H-1.4 V0 H-7.4 Z M1.4 -3 H6.2 L7.4 0 H1.4 Z"/>`
      + `<path class="shirt" d="M-8 -44.4 Q0 -46.2 8 -44.4 L7.2 -26 H-7.2 Z"/>`
      + `<path class="sleeve shirt" d="M-7.6 -43.4 L-11 -30.6 M7.6 -43.4 L11 -30.6"/>`
      + `<circle class="skin" cx="-11.4" cy="-29.2" r="1.9"/><circle class="skin" cx="11.4" cy="-29.2" r="1.9"/>`
      + `<rect class="skin" x="-1.8" y="-47.4" width="3.6" height="3.4"/><circle class="skin" cx="0" cy="-53" r="7"/>`
      + `<circle class="eye" cx="-2.5" cy="-53.4" r=".85"/><circle class="eye" cx="2.5" cy="-53.4" r=".85"/>`
      + `<circle class="cheek" cx="-4.2" cy="-50.6" r="1.3"/><circle class="cheek" cx="4.2" cy="-50.6" r="1.3"/>`
      + `<path class="smile" d="M-2 -50 Q0 -48.4 2 -50"/>`
      + `<path class="hair" d="M-7.3 -52 C-8 -60 -3 -61.6 0 -61.6 C4 -61.6 8 -59 7.3 -52 C5 -56.5 -2 -57.5 -7.3 -52 Z"/></g>`;
  }

  // ---------- landing page ----------

  function field(el, pending = 0, owned = []) {
    const rand = rng(1913);
    const sway = (x, r) => -(x / 180) - r() * 0.6; // a wave of wind from left to right
    const items = owned.filter((k) => FARM[k]);
    const clear = items.map((k) => [FARM[k][0] - FARM[k][1] / 2, FARM[k][0] + FARM[k][1] / 2]);
    const back = stalks(rand, 0, 1000, 118, 90, 46, 62, sway);
    // Front stalks are drawn from the same seed whatever is owned; the ones in an item's spot are left out.
    const front = stalks(rand, 0, pending ? 800 : 1000, 126, pending ? 60 : 75, 58, 82, sway)
      .split(/(?=<g transform="translate\()/).filter((g) => {
        const x = +g.match(/translate\(([\d.]+)/)?.[1];
        return !clear.some(([a, b]) => x >= a && x <= b);
      }).join("");
    el.innerHTML = `<svg class="harvest-field" viewBox="0 0 1000 130" preserveAspectRatio="xMidYMax slice" aria-hidden="true">
      <circle class="sun" cx="930" cy="34" r="20"/>
      <path class="ground" d="M0 120 Q500 108 1000 120 V130 H0 Z"/>
      <g class="wheat back">${back}</g>
      <g class="farm">${items.map((k) => `<g transform="translate(${FARM[k][0]} 123)"><g class="farm-item ${k}">${FARM[k][2]()}</g></g>`).join("")}</g>
      <g class="wheat">${front}</g>
      <g transform="translate(535 128)"><g class="farmer">${farmer()}</g></g>
      ${pending ? `<g class="field-stack" transform="translate(905 124) scale(${(0.55 + 0.45 * Math.min(1, pending / 200)).toFixed(2)})">`
        + `<title>${pending} objects waiting in the haystack</title>${haystack()}</g>` : ""}
    </svg>`;
  }

  // ---------- vetter header haystack ----------

  let stackEl = null, pendingNow = null, reaping = false;

  // Each queue (random mode, or one saved filter's) has its own stack and so its own "full".
  let stackName = "";
  const stackKey = () => STACK_KEY + (stackName ? `:${stackName}` : "");

  function loadFull() {
    try { return JSON.parse(localStorage.getItem(stackKey()))?.full || 0; } catch { return 0; }
  }

  function saveFull(full) {
    try { localStorage.setItem(stackKey(), JSON.stringify({ full })); } catch {}
  }

  function useStack(name) {
    stackName = name || "";
  }

  function mountStack(el) {
    stackEl = el;
    el.innerHTML = `<svg viewBox="-56 -84 112 88" aria-hidden="true"><path class="stubble" d="M-46 0 H46"/>`
      + `<g class="stack-scale">${haystack()}</g></svg><span class="stack-n"></span>`;
  }

  // Size follows pending / pending-right-after-the-latest-batch, so vetting shrinks it.
  function setStack(pending, refill = false) {
    pendingNow = pending;
    if (!stackEl || reaping) return;
    let full = loadFull();
    if (refill || pending > full) saveFull((full = pending));
    const s = pending ? 0.3 + 0.7 * Math.sqrt(pending / Math.max(1, full)) : 0;
    stackEl.style.setProperty("--s", s.toFixed(3));
    stackEl.classList.toggle("empty", !pending);
    stackEl.title = pending ? `${pending} left in the haystack` : "Haystack vetted: sample a batch to harvest more";
    stackEl.querySelector(".stack-n").textContent = pending || "";
  }

  // ---------- overlays ----------

  function overlay(cls, svg, caption, ms) {
    document.querySelector(".harvest-overlay")?.remove();
    const el = document.createElement("div");
    el.className = `harvest-overlay ${cls}`;
    el.title = "Click to dismiss";
    el.innerHTML = `${svg}<div class="caption">${caption}</div>`;
    document.body.appendChild(el);
    return new Promise((done) => {
      const close = () => {
        if (!el.isConnected) return done();
        el.classList.add("leaving");
        setTimeout(() => { el.remove(); done(); }, 300);
      };
      el.onclick = close;
      setTimeout(close, ms);
    });
  }

  // Scythe through the wheat, tractor hauls it to the stack, then the header stack refills.
  async function reap(n) {
    if (calm()) return setStack(pendingNow ?? n, true);
    reaping = true;
    const rand = rng(Date.now() & 0xffff);
    const cutAt = (x) => 0.25 + (x - 30) / 540 * 1.2; // when the scythe passes x
    let wheat = "";
    for (let i = 0; i < 46; i++) {
      const x = 40 + 520 * (i + rand()) / 46;
      wheat += stalk(x, 146 + rand() * 4, 52 + rand() * 26, (rand() - 0.5) * 10, cutAt(x), "cut");
    }
    const svg = `<svg viewBox="0 0 1000 160" aria-hidden="true">
      <path class="ground" d="M0 148 Q500 140 1000 148 V160 H0 Z"/>
      <g class="wheat">${wheat}</g>
      <g transform="translate(880 150)"><g class="grow">${haystack()}</g></g>
      <g class="scythe-run"><g class="scythe"><path class="snath" d="M0 0 L-26 -92"/><path class="blade" d="M0 0 C18 6 44 2 62 -14 C44 -6 20 -4 2 -6 Z"/></g></g>
      <g class="tractor-run">${tractor()}</g>
    </svg>`;
    await overlay("reap", svg, `Harvested <b>${n}</b> object${n === 1 ? "" : "s"} into the haystack`, 4300);
    reaping = false;
    if (pendingNow !== null) setStack(pendingNow, true);
    stackEl?.classList.remove("bump");
    void stackEl?.offsetWidth; // restart the bump animation
    stackEl?.classList.add("bump");
  }

  // The stack shakes, straw flies and a needle rises out of it.
  function needle(find) {
    const rare = find.kind === "rare";
    const rand = rng(99);
    let straw = "";
    for (let i = 0; i < 14; i++) {
      const dx = (rand() - 0.5) * 220, dy = -40 - rand() * 70, r = (rand() - 0.5) * 540;
      straw += `<path style="--dx:${dx.toFixed(0)}px;--dy:${dy.toFixed(0)}px;--r:${r.toFixed(0)}deg;--d:${(0.1 + rand() * 0.6).toFixed(2)}s" d="M-5 0 h10"/>`;
    }
    const svg = `<svg viewBox="0 0 400 190" aria-hidden="true">
      <path class="ground" d="M0 178 Q200 172 400 178 V190 H0 Z"/>
      <g transform="translate(200 178)">
        <g class="needle-rise"><g transform="rotate(18)">
          <path class="needle-shaft" d="M0 0 L2 -112 Q0 -116 -2 -112 Z"/><ellipse class="needle-eye" cx="0" cy="-104" rx=".8" ry="4"/>
          <g transform="translate(0 -114)"><path class="sparkle" d="M0 -14 L3 -3 L14 0 L3 3 L0 14 L-3 3 L-14 0 L-3 -3 Z"/></g>
        </g></g>
        <g transform="scale(1.3)"><g class="rummage">${haystack()}</g></g>
        <g class="flying" transform="translate(0 -88)">${straw}</g>
      </g>
    </svg>`;
    const what = find.type ? ` (${esc(find.type)})` : "";
    const caption = rare
      ? `Once in a lifetime: a needle in the haystack! <b>${esc(find.name)}</b>${what}`
      : `Found a needle in the haystack: <b>${esc(find.name)}</b>${what}`;
    return overlay(`needle${rare ? " rare" : ""}${calm() ? " still" : ""}`, svg, caption, rare ? 6000 : 4800);
  }

  return { field, farmIcon, fitIcons, mountStack, useStack, setStack, reap, needle };
})();
