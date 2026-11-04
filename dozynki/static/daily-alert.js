// On every page (loaded with the tab bar): a pop-up when your latest daily report has jobs that couldn't run,
// with a button to run just those again now. It says so while they rerun, and how it went.

(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const POLL = 5000;
  let box = null;
  let poll = null;

  async function post(path) {
    const r = await fetch(path, { method: "POST" });
    if (!r.ok) {
      let msg = (await r.text()).slice(0, 300);
      try { msg = JSON.parse(msg).detail; } catch {}
      throw new Error(msg);
    }
    return r.json();
  }

  function show(html) {
    if (!box) {
      box = document.createElement("div");
      box.className = "daily-alert";
      box.setAttribute("role", "alertdialog");
      box.setAttribute("aria-label", "Daily jobs");
      document.body.append(box);
    }
    box.innerHTML = html;
    return box;
  }

  function close() {
    box?.remove();
    box = null;
  }

  function changed() {
    window.dispatchEvent(new Event("daily:changed"));  // the Daily page reloads its reports
  }

  function failed(a) {
    const b = show(`<div class="da-head"><b>Daily jobs couldn't all run</b><span class="muted">night of ${esc(a.night)}</span></div>
      <ul>${a.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>
      ${a.headline ? `<div class="muted">The report says: ${esc(a.headline)}</div>` : ""}
      <div class="da-actions"><button class="primary" data-act="rerun">Rerun failed jobs</button>
        <a href="/daily">Open report</a><button data-act="dismiss">Dismiss</button></div>
      <div class="da-err err"></div>`);
    b.querySelector("[data-act=rerun]").onclick = async (e) => {
      e.target.disabled = true;
      try {
        await post(`/api/daily/rerun/${a.id}`);
        changed();
        running(a);
      } catch (err) {
        b.querySelector(".da-err").textContent = "Couldn't rerun: " + err.message;
        e.target.disabled = false;
      }
    };
    b.querySelector("[data-act=dismiss]").onclick = async () => {
      close();
      try { await post(`/api/daily/dismiss/${a.id}`); } catch {}
    };
  }

  function running(a) {
    show(`<div class="da-head"><b>Rerunning the daily jobs…</b><span class="muted">night of ${esc(a.night)}</span></div>
      <div class="muted">A filter waits for its background check, which can take a while on a big night.</div>`);
    clearTimeout(poll);
    poll = setTimeout(() => check(true), POLL);
  }

  async function check(after_rerun = false) {
    let a;
    try {
      const r = await fetch("/api/daily/alert");
      if (!r.ok) return;
      a = (await r.json()).alert;
    } catch {
      return;
    }
    if (a?.running) return after_rerun || box ? running(a) : null;  // a scheduled run isn't news until it's done
    if (after_rerun) changed();
    if (a) return failed(a);
    if (!after_rerun) return close();
    const b = show(`<div class="da-head"><b>The daily jobs ran this time</b></div>
      <div class="da-actions"><a href="/daily">Open report</a><button data-act="close">Close</button></div>`);
    b.querySelector("[data-act=close]").onclick = close;
  }

  check();
})();
