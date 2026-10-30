// The population an analysis page uses: the uniform random sample, every queued object, or one object list.
// Pages keep it as { all_sources, object_list } in their state; this fills the <select> and maps it back.
const Population = (() => {
  const value = (s) => (s.object_list ? `list:${s.object_list}` : s.all_sources ? "all" : "random");

  // Fills sel and calls onchange({ all_sources, object_list }) when it changes. A remembered list that
  // was deleted falls back to the random sample, and onchange is called so the page reloads with it.
  async function mount(sel, state, onchange) {
    let lists = [];
    try { lists = await (await fetch("/api/lists")).json(); } catch {}
    const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    sel.innerHTML = `<option value="random">random sample</option><option value="all">everything queued</option>`
      + (lists.length ? `<optgroup label="Object lists">${lists.map((l) =>
        `<option value="list:${esc(l.name)}">${esc(l.name)} (${l.n})</option>`).join("")}</optgroup>` : "");
    sel.title = "Which objects to use: the uniform random sample, every queued object, or an object list loaded in the vetter";
    const gone = state.object_list && !lists.some((l) => l.name === state.object_list);
    sel.value = gone ? "random" : value(state);
    sel.onchange = () => onchange(parse(sel.value));
    if (gone) onchange(parse("random"));
  }

  const parse = (v) => ({ all_sources: v === "all", object_list: v.startsWith("list:") ? v.slice(5) : "" });

  // what the population is called in a sentence ("in the random sample")
  const phrase = (s) => (s.object_list ? `on the list "${s.object_list}"` : s.all_sources ? "in the queue" : "in the random sample");

  return { mount, phrase };
})();
