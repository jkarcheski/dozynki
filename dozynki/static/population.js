// The population an analysis page uses: the uniform random sample, every queued object, one object list, or the
// objects I vetted on a range of dates, and of those the LSST objects, the ZTF objects or both. Pages keep it as
// { all_sources, object_list, vetted, survey } in their state (vetted: "" or "FROM..TO", UTC dates, either end may
// be empty; survey: lsst | ztf | both); this fills the <select>s and maps them back.
const Population = (() => {
  const value = (s) => (s.object_list ? `list:${s.object_list}` : s.vetted ? "vetted" : s.all_sources ? "all" : "random");

  // Fills sel and calls onchange({ all_sources, object_list, vetted, survey }) when it changes. A remembered list that
  // was deleted falls back to the random sample, and onchange is called so the page reloads with it.
  // Choosing "vetted by me" shows from/to date boxes after sel; the survey picker follows them.
  async function mount(sel, state, onchange) {
    let lists = [];
    try { lists = await (await fetch("/api/lists")).json(); } catch {}
    const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    sel.innerHTML = `<option value="random">random sample</option><option value="all">everything queued</option>`
      + `<option value="vetted">vetted by me…</option>`
      + (lists.length ? `<optgroup label="Object lists">${lists.map((l) =>
        `<option value="list:${esc(l.name)}">${esc(l.name)} (${l.n})</option>`).join("")}</optgroup>` : "");
    sel.title = "Which objects to use: the uniform random sample, every queued object, the objects you first vetted "
      + "between two dates, or an object list loaded in the vetter";

    let dates = sel.parentElement.nextElementSibling;
    if (!dates?.classList.contains("vetted-dates")) {
      dates = document.createElement("span");
      dates.className = "vetted-dates";
      dates.title = "Objects you first labeled on these dates (UTC), inclusive; leave one empty for an open end";
      dates.innerHTML = `from <input type="date" aria-label="Vetted from"> to <input type="date" aria-label="Vetted to">`;
      dates.style.cssText = "display:inline-flex;gap:4px;align-items:center";
      sel.parentElement.after(dates);
    }
    let surveyLabel = dates.nextElementSibling;
    if (!surveyLabel?.classList.contains("survey-pick")) {
      surveyLabel = document.createElement("label");
      surveyLabel.className = "survey-pick";
      surveyLabel.title = "Objects ANTARES has from LSST (an LSST diaObject) or from ZTF (a ZTF object, so an LSST "
        + "object ZTF also detected is in both); both: all of them. The random sample is each survey's own uniform sample.";
      surveyLabel.innerHTML = `survey <select aria-label="Which survey"><option value="lsst">LSST</option>`
        + `<option value="ztf">ZTF</option><option value="both">both</option></select>`;
      dates.after(surveyLabel);
    }
    const survey = surveyLabel.querySelector("select");
    survey.value = state.survey || "lsst";
    const [from, to] = dates.querySelectorAll("input");
    [from.value, to.value] = (state.vetted || "..").split("..");
    const show = () => (dates.style.display = sel.value === "vetted" ? "inline-flex" : "none");  // not hidden: inline display wins
    const changed = () => { show(); onchange(parse(sel.value, from.value, to.value, survey.value)); };

    const gone = state.object_list && !lists.some((l) => l.name === state.object_list);
    sel.value = gone ? "random" : value(state);
    show();
    sel.onchange = from.onchange = to.onchange = survey.onchange = changed;
    if (gone) onchange(parse("random", "", "", survey.value));
  }

  // "vetted" with no dates still needs a value, so it is "..": everything I vetted
  const parse = (v, from = "", to = "", survey = "lsst") => ({ all_sources: v === "all",
    object_list: v.startsWith("list:") ? v.slice(5) : "", vetted: v === "vetted" ? `${from}..${to}` : "", survey });

  // the population fields of an API request
  const params = (s) => ({ all_sources: s.all_sources, object_list: s.object_list || "", vetted: s.vetted || "",
    survey: s.survey || "lsst" });

  // what the population is called in a sentence ("in the random sample")
  function phrase(s) {
    const of = { ztf: " (ZTF)", both: " (LSST and ZTF)" }[s.survey] || "";
    if (s.object_list) return `on the list "${s.object_list}"${of}`;
    if (s.vetted) {
      const [from, to] = s.vetted.split("..");
      return "vetted by me" + (from && to ? ` from ${from} to ${to}` : from ? ` since ${from}` : to ? ` through ${to}` : "") + of;
    }
    return (s.all_sources ? "in the queue" : "in the random sample") + of;
  }

  return { mount, params, phrase };
})();
