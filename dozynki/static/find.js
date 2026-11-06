// The tab bar's search box (app.py _nav): finds an object by TNS name or LSST ID (/api/search) and marks it on
// the page. A page that shows objects sets window.onFind = async (hit) => null once it has marked the object, or a
// note saying why it can't (hit: {object_id, ra, dec, kind, sep}); Find.mark rings the object's rows and links.
// On a page without one, the object opens in the vetter.
const Find = (() => {
  const form = document.querySelector("form.find");
  if (!form) return null;
  const box = form.querySelector("input");
  const note = document.createElement("div");
  note.className = "find-note";
  note.setAttribute("role", "status");
  note.hidden = true;
  form.append(note);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const vetUrl = (id, as) => `/vet?object=${encodeURIComponent(id)}${as ? `&as=${encodeURIComponent(as)}` : ""}`;

  function say(html) {
    note.innerHTML = html;
    note.hidden = !html;
  }

  // Ring the rows (or list items) linking to the object, and scroll the first into view; false if there are none.
  function mark(id) {
    document.querySelectorAll(".found").forEach((el) => el.classList.remove("found"));
    const links = [...document.querySelectorAll("a[href]")].filter((a) => {
      const u = new URL(a.href, location.href);
      return u.pathname === "/vet" && u.searchParams.get("object") === id;
    });
    const els = links.map((a) => a.closest("tr, li") || a);
    els.forEach((el) => el.classList.add("found"));
    links.forEach((a) => { for (let d = a.closest("details"); d; d = d.parentElement.closest("details")) d.open = true; });
    els[0]?.scrollIntoView({ block: "center", behavior: "smooth" });
    return els.length > 0;
  }

  async function find(q) {
    q = q.trim();
    if (!q) return;
    say("");
    box.classList.add("busy");
    let hit;
    try {
      const r = await fetch(`/api/search?${new URLSearchParams({ q })}`);
      if (!r.ok) {
        let detail = await r.text();
        try { detail = JSON.parse(detail).detail; } catch {}
        throw new Error(detail);
      }
      hit = await r.json();
    } catch (e) {
      box.setCustomValidity(e.message);
      box.reportValidity();
      return;
    } finally {
      box.classList.remove("busy");
    }
    hit.query = q;
    if (!window.onFind) return void (location.href = vetUrl(hit.object_id, hit.object_id === q ? "" : q));
    const why = await window.onFind(hit);
    if (why) say(`${esc(q)} ${esc(why)}. <a href="${vetUrl(hit.object_id, q)}">Open in the vetter</a>`);
  }

  form.onsubmit = (e) => { e.preventDefault(); find(box.value); };
  box.oninput = () => { box.setCustomValidity(""); say(""); };
  document.addEventListener("click", (e) => { if (!form.contains(e.target)) say(""); });
  return { find, mark, box };
})();
