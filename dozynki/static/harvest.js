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

  // ---------- farm (shop items and unlocks, farm.py) ----------

  // Each item with its base center at the origin; x is its spot in the 1000-wide field, w the
  // width of front wheat it clears so it shows.
  const FARM = {
    windmill: [60, 70, () => `<path class="mill" d="M-12 0 L-7 -78 H7 L12 0 Z"/><path class="roof" d="M-10 -76 L0 -90 L10 -76 Z"/>`
      + `<rect class="door" x="-4" y="-14" width="8" height="14" rx="4"/>`
      + `<g transform="translate(0 -82)"><g class="sails">${[0, 90, 180, 270].map((a) =>
        `<g transform="rotate(${a})"><path class="spar" d="M0 0 V-40"/><rect class="sail" x="1" y="-40" width="9" height="30"/></g>`).join("")}`
      + `<circle class="hub" r="3"/></g></g>`],
    weathervane: [135, 30, () => `<path class="spar" d="M0 0 V-96"/><path class="vane" d="M-16 -88 H14 M14 -88 l-6 -4 M14 -88 l-6 4"/>`
      + `<path class="vane-tail" d="M-16 -88 l-6 -6 v12 Z"/><path class="vane" d="M-6 -78 H6 M0 -84 V-72"/>`],
    barn: [215, 90, () => `<path class="barn" d="M-38 0 V-46 L-28 -66 L0 -78 L28 -66 L38 -46 V0 Z"/>`
      + `<path class="trim" d="M-38 -46 L-28 -66 L0 -78 L28 -66 L38 -46"/>`
      + `<rect class="trim-fill" x="-13" y="-32" width="26" height="32"/><path class="trim" d="M-13 -32 L13 0 M13 -32 L-13 0"/>`
      + `<rect class="trim-fill" x="-6" y="-60" width="12" height="10"/>`],
    stork: [290, 40, () => `<path class="spar" d="M0 0 V-90"/><ellipse class="nest" cx="0" cy="-92" rx="16" ry="5"/>`
      + `<ellipse class="bird" cx="1" cy="-102" rx="10" ry="6"/><path class="bird-wing" d="M-8 -102 Q2 -96 10 -101 Q2 -104 -8 -102 Z"/>`
      + `<path class="neck" d="M8 -105 Q12 -116 10 -122"/><circle class="bird" cx="10" cy="-123" r="3"/>`
      + `<path class="beak" d="M12 -123 l9 3"/>`],
    rocks: [338, 46, () => `<ellipse class="rock" cx="-11" cy="-5" rx="11" ry="7"/><ellipse class="rock" cx="9" cy="-4" rx="9" ry="5"/>`
      + `<ellipse class="rock" cx="-1" cy="-10" rx="7" ry="6"/>`],
    well: [388, 44, () => `<path class="spar" d="M-13 -12 V-42 M13 -12 V-42"/><path class="roof" d="M-19 -40 L0 -52 L19 -40 Z"/>`
      + `<path class="rope" d="M0 -42 V-24"/><rect class="bucket" x="-3" y="-24" width="6" height="6"/>`
      + `<rect class="stone" x="-16" y="-16" width="32" height="16" rx="2"/><path class="trim" d="M-16 -8 H16 M-6 -16 V-8 M6 -8 V0"/>`],
    beehives: [440, 52, () => [-12, 12].map((x) => `<g transform="translate(${x} 0)">`
      + `<path class="skep" d="M-9 0 C-10 -12 -6 -20 0 -20 C6 -20 10 -12 9 0 Z"/>`
      + `<path class="trim" d="M-9 -5 H9 M-9 -10 H9 M-7 -15 H7"/><circle class="door" cy="-3" r="2"/></g>`).join("")
      + `<g class="bees"><circle cx="-4" cy="-28" r="1.4"/><circle cx="7" cy="-32" r="1.4"/></g>`],
    cow: [503, 66, () => `<rect class="cow" x="-24" y="-30" width="40" height="18" rx="7"/>`
      + `<path class="leg" d="M-18 -14 V0 M-10 -14 V0 M6 -14 V0 M12 -14 V0"/><path class="spot" d="M-14 -28 q6 3 2 9 q-7 0 -6 -9 Z M2 -26 q8 0 6 8 q-8 2 -6 -8 Z"/>`
      + `<rect class="cow" x="14" y="-36" width="12" height="11" rx="4"/><path class="horn" d="M16 -36 l-2 -4 M24 -36 l2 -4"/>`
      + `<path class="tail" d="M-24 -26 q-5 6 -3 12"/>`],
    horse: [578, 66, () => `<rect class="horse" x="-22" y="-34" width="36" height="16" rx="7"/>`
      + `<path class="horse-leg" d="M-17 -20 V0 M-10 -20 V0 M5 -20 V0 M11 -20 V0"/>`
      + `<path class="horse" d="M8 -32 L16 -50 L26 -46 L28 -40 L20 -40 L16 -30 Z"/><path class="mane" d="M10 -32 L17 -50"/>`
      + `<path class="mane" d="M-22 -30 q-8 8 -5 20"/>`],
    geese: [646, 64, () => [[-20, 0], [0, -2], [20, 1]].map(([x, y], i) => `<g transform="translate(${x} ${y})${i === 1 ? " scale(-1 1)" : ""}">`
      + `<ellipse class="goose" cx="0" cy="-7" rx="8" ry="5"/><path class="goose-neck" d="M5 -9 Q8 -16 7 -20"/>`
      + `<circle class="goose" cx="7" cy="-20" r="2.6"/><path class="beak" d="M9 -20 l4 1"/><path class="foot" d="M-1 -2 V0 M2 -2 V0"/></g>`).join("")],
    sunflowers: [706, 60, () => [[-20, 58], [-6, 70], [8, 62], [20, 74]].map(([x, h]) =>
      `<g transform="translate(${x} 0)"><path class="leafy" d="M0 0 V${-h}"/><path class="leaf" d="M0 ${-h / 2} q7 -6 9 0 q-6 4 -9 0 Z"/>`
      + `<circle class="petals" cy="${-h}" r="7"/><circle class="seeds" cy="${-h}" r="3.4"/></g>`).join("")],
    poppies: [762, 54, () => [[-20, 40], [-10, 50], [0, 36], [10, 54], [20, 44]].map(([x, h]) =>
      `<g transform="translate(${x} 0)"><path class="leafy thin" d="M0 0 Q2 ${-h / 2} 0 ${-h}"/><circle class="poppy" cy="${-h}" r="4"/>`
      + `<circle class="seeds" cy="${-h}" r="1.2"/></g>`).join("")],
    lantern: [800, 30, () => `<path class="spar" d="M0 0 V-80 H14"/><path class="rope" d="M12 -80 V-72"/>`
      + `<circle class="glow" cx="12" cy="-62" r="12"/><rect class="lamp" x="7" y="-72" width="10" height="16" rx="2"/>`
      + `<path class="trim" d="M7 -64 H17"/>`],
    telescope: [838, 40, () => `<path class="leg" d="M0 -30 L-12 0 M0 -30 L12 0 M0 -30 V0"/>`
      + `<g transform="translate(0 -32) rotate(-38)"><rect class="tube" x="-16" y="-5" width="36" height="10" rx="2"/>`
      + `<rect class="tube-end" x="18" y="-6" width="5" height="12" rx="1"/></g>`],
    wreath: [975, 40, () => `<path class="spar" d="M0 0 V-62"/>`
      + `<path class="crown" d="M-16 -62 C-18 -86 -8 -100 0 -100 C8 -100 18 -86 16 -62 Z"/>`
      + `<path class="trim" d="M-14 -72 H14 M-10 -86 H10 M0 -100 V-62"/>`
      + `<path class="ribbon red" d="M-14 -62 q-4 12 -1 22"/><path class="ribbon white" d="M-9 -62 q-2 10 1 18"/>`
      + `<path class="ribbon red" d="M9 -62 q2 10 -1 18"/><path class="ribbon white" d="M14 -62 q4 12 1 22"/>`],
  };

  // A small picture of one item for the shop, framed on a strip of ground.
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

  // ---------- farmer (farm.py WARDROBE) ----------

  // Standing with the feet at the origin, about 66 tall; wear is {slot: key or null}, null drawing the default
  // (short hair, work clothes, shoes, no hat). Back to front: back hair, legs, boots, skirt, torso, arms, head, hat.
  const SHORT = `<path class="hair" d="M-7.3 -52 C-8 -60 -3 -61.6 0 -61.6 C4 -61.6 8 -59 7.3 -52 C5 -56.5 -2 -57.5 -7.3 -52 Z"/>`;
  const HAIR = {
    long_hair: [`<path class="hair" d="M-8 -54 C-9 -63 9 -63 8 -54 L9.5 -39 Q0 -37 -9.5 -39 Z"/>`, SHORT],
    bun: ["", `<circle class="hair" cx="0" cy="-62.5" r="3.8"/>${SHORT}`],
    curly: ["", [[-7.4, -54], [-6.4, -58.6], [-3, -61.6], [1.4, -62.2], [5.4, -60], [7.6, -55.4]]
      .map(([x, y]) => `<circle class="hair" cx="${x}" cy="${y}" r="3.2"/>`).join("")],
    braids: ["", SHORT + [-1, 1].map((side) => [0, 1, 2, 3, 4].map((k) =>
      `<ellipse class="hair" cx="${side * (7.4 + k * 0.3)}" cy="${-50 + k * 3}" rx="2" ry="2.1"/>`).join("")
      + `<path class="ribbon red" d="M${side * 8.6} -36.4 l${side * 2} 3.4 M${side * 8.6} -36.4 l${-side * 1} 3.8"/>`).join("")],
  };
  const SKIRT_STRIPES = ["orange", "green", "magenta", "orange", "yellow", "blue", "orange", "green", "magenta", "orange"];
  const OUTFIT = {
    // [legs, skirt, shirt class, overlay]
    default: [`<path class="trousers" d="M-7 -27 H7 L6 -3 H1.6 L0 -20 L-1.6 -3 H-6 Z"/>`, "", "shirt", ""],
    flannel: [`<path class="denim" d="M-7 -27 H7 L6 -3 H1.6 L0 -20 L-1.6 -3 H-6 Z"/>`, "", "flannel",
      `<path class="check" d="M-7.6 -39 H7.6 M-7.3 -33 H7.3 M-3 -45 V-26 M3 -45 V-26"/>`],
    overalls: [`<path class="denim" d="M-7 -27 H7 L6 -3 H1.6 L0 -20 L-1.6 -3 H-6 Z"/>`, "", "shirt light",
      `<rect class="denim" x="-5.4" y="-38" width="10.8" height="12"/><path class="strap" d="M-4.6 -38 L-6 -44.6 M4.6 -38 L6 -44.6"/>`
      + `<rect class="pocket" x="-2.6" y="-35" width="5.2" height="4" rx=".6"/>`],
    lowicz: [`<path class="stocking" d="M-3.6 -9 V-3 M3.6 -9 V-3"/>`,
      SKIRT_STRIPES.map((c, i) => { // slices of a flared skirt, -7..7 at the waist and -12..12 at the hem
        const a = -7 + 1.4 * i, b = a + 1.4, c0 = -12 + 2.4 * i, c1 = c0 + 2.4;
        return `<path class="stripe ${c}" d="M${a} -29 L${b} -29 L${c1} -8 L${c0} -8 Z"/>`;
      }).join("") + `<path class="hem" d="M-12 -8.6 H12"/>`, "shirt blouse",
      `<path class="vest black" d="M-7.2 -41 H7.2 L6.6 -28 H-6.6 Z"/>`
      + [[-3.6, -37, "red"], [3.6, -37, "yellow"], [-3.6, -32, "blue"], [3.6, -32, "red"], [0, -34.5, "green"]]
        .map(([x, y, c]) => `<circle class="bloom ${c}" cx="${x}" cy="${y}" r="1.3"/>`).join("")
      + [-3, -1, 1, 3].map((x) => `<circle class="bead" cx="${x}" cy="${-44.4 + Math.abs(x) * .35}" r=".9"/>`).join("")],
    krakow: [`<path class="trousers red" d="M-7 -27 H7 L6 -3 H1.6 L0 -20 L-1.6 -3 H-6 Z"/>`
      + `<path class="pinstripe" d="M-5.6 -26 L-5 -4 M-3.2 -26 L-3 -4 M3.2 -26 L3 -4 M5.6 -26 L5 -4"/>`, "", "shirt blouse",
      `<path class="vest navy" d="M-7.4 -43.4 L-1.2 -43.4 L-1.2 -28 H-6.8 Z M7.4 -43.4 L1.2 -43.4 L1.2 -28 H6.8 Z"/>`
      + `<path class="tassel" d="M-4 -40 v3 M-4 -35 v3 M4 -40 v3 M4 -35 v3"/><rect class="belt" x="-7.2" y="-28.6" width="14.4" height="2.6"/>`],
  };
  const BOOTS = {
    default: `<path class="shoe" d="M-6.2 -3 H-1.4 V0 H-7.4 Z M1.4 -3 H6.2 L7.4 0 H1.4 Z"/>`,
    wellies: `<path class="welly" d="M-6.4 -12 H-1.2 V0 H-7.8 Q-8 -1.6 -6.4 -2.2 Z M1.2 -12 H6.4 V-2.2 Q8 -1.6 7.8 0 H1.2 Z"/>`,
    cowboy_boots: `<path class="leather" d="M-6.4 -11 H-1.4 V-1.8 L-1 0 H-9.6 Q-9.4 -2.2 -6.4 -2.6 Z M1.4 -11 H6.4 V-2.6 Q9.4 -2.2 9.6 0 H1 L1.4 -1.8 Z"/>`
      + `<path class="stitch" d="M-5.4 -9 Q-4 -7 -2.4 -9 M2.4 -9 Q4 -7 5.4 -9"/>`,
  };
  const HATS = {
    straw_hat: `<ellipse class="straw-hat" cx="0" cy="-58" rx="13" ry="2.4"/><path class="straw-hat" d="M-6 -58 Q-6.4 -66.4 0 -66.4 Q6.4 -66.4 6 -58 Z"/>`
      + `<path class="band" d="M-6.2 -59.6 H6.2"/>`,
    flat_cap: `<path class="cap" d="M-7.6 -55.6 Q-7.4 -63 0 -63 Q7 -62.4 7.8 -57 L13 -56 Q8 -54.2 -7.6 -55.6 Z"/>`,
    cowboy_hat: `<path class="cowboy" d="M-6.6 -57.6 L-6 -66.4 Q-3 -64.4 0 -66.6 Q3 -64.4 6 -66.4 L6.6 -57.6 Z"/>`
      + `<path class="cowboy" d="M-14.6 -60.4 Q-12.4 -55.4 0 -56.2 Q12.4 -55.4 14.6 -60.4 Q10 -57.2 0 -58 Q-10 -57.2 -14.6 -60.4 Z"/>`
      + `<path class="band dark" d="M-6.4 -59.4 H6.4"/>`,
    rogatywka: `<path class="feather-stem" d="M4.4 -64 Q8.6 -73 7.2 -84"/><ellipse class="feather" cx="7.2" cy="-84.6" rx="2.8" ry="4.4"/>`
      + `<circle class="feather-eye" cx="7.2" cy="-84" r="1.3"/>`
      + `<path class="crimson" d="M-8.4 -57 L-9.6 -66.4 L0 -63.4 L9.6 -66.4 L8.4 -57 Z"/><rect class="fur" x="-8.6" y="-59.4" width="17.2" height="3.4" rx="1"/>`,
  };

  function farmer(wear = {}) {
    const [hairBack, hairFront] = HAIR[wear.hair] || ["", SHORT];
    const [legs, skirt, shirt, overlay] = OUTFIT[wear.outfit] || OUTFIT.default;
    return `<g class="farmer-figure">${hairBack}${legs}${BOOTS[wear.boots] || BOOTS.default}${skirt}`
      + `<path class="${shirt}" d="M-8 -44.4 Q0 -46.2 8 -44.4 L7.2 -26 H-7.2 Z"/>${overlay}`
      + `<path class="sleeve ${shirt}" d="M-7.6 -43.4 L-11 -30.6 M7.6 -43.4 L11 -30.6"/>`
      + `<circle class="skin" cx="-11.4" cy="-29.2" r="1.9"/><circle class="skin" cx="11.4" cy="-29.2" r="1.9"/>`
      + `<rect class="skin" x="-1.8" y="-47.4" width="3.6" height="3.4"/><circle class="skin" cx="0" cy="-53" r="7"/>`
      + `<circle class="eye" cx="-2.5" cy="-53.4" r=".85"/><circle class="eye" cx="2.5" cy="-53.4" r=".85"/>`
      + `<circle class="cheek" cx="-4.2" cy="-50.6" r="1.3"/><circle class="cheek" cx="4.2" cy="-50.6" r="1.3"/>`
      + `<path class="smile" d="M-2 -50 Q0 -48.4 2 -50"/>${hairFront}${HATS[wear.hat] || ""}</g>`;
  }

  // The farmer alone; slot zooms in on the part a wardrobe item changes.
  const ZOOM = { hat: "-19 -91 38 40", hair: "-16 -66 32 32", outfit: "-17 -50 34 52", boots: "-13 -16 26 19" };

  function farmerIcon(wear = {}, cls = "farm-icon", slot = null) {
    return `<svg class="${cls}" viewBox="${ZOOM[slot] || "-30 -90 60 96"}" aria-hidden="true">`
      + `<path class="ground" d="M-30 0 H30 V6 H-30 Z"/>${farmer(wear)}</svg>`;
  }

  // ---------- landing page ----------

  function field(el, pending = 0, owned = [], wear = {}) {
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
      <g transform="translate(535 128)"><g class="farmer">${farmer(wear)}</g></g>
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

  return { field, farmIcon, farmerIcon, fitIcons, mountStack, useStack, setStack, reap, needle };
})();
