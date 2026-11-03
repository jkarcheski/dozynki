// DOZYNKI profile page: sign in, make a profile, reset a password, your YSE PZ login. Plain JS, no build step.

const $ = (s) => document.querySelector(s);

async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  if (!r.ok) {
    let msg = await r.text();
    try { msg = JSON.parse(msg).detail || msg; } catch { /* plain text */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json();
}

function show(...ids) {
  for (const el of document.querySelectorAll("main > section, main > form")) el.hidden = !ids.includes(el.id);
}

// Submit a form's fields to path; on success run done(result).
function wire(form, path, done, extra = () => ({})) {
  form.onsubmit = async (e) => {
    e.preventDefault();
    const err = form.querySelector(".err");
    const button = form.querySelector("button");
    const data = Object.fromEntries(new FormData(form));
    for (const box of form.querySelectorAll("input[type=checkbox]")) data[box.name] = box.checked;
    err.textContent = "";
    button.disabled = true;
    try {
      done(await api(path, { ...data, ...extra() }));
    } catch (ex) {
      err.textContent = ex.message;
    } finally {
      button.disabled = false;
    }
  };
}

const home = () => { location.href = "/"; };

function showYse(username) {
  $("#yse-on").hidden = !username;
  $("#yse-form").hidden = !!username;
  $("#yse-user").textContent = username || "";
  $("#yse-form").reset();
}

// The outcome of a save or test, in a box under the card, so a wrong password can't go unnoticed.
function yseStatus(ok, text) {
  const el = $("#yse-status");
  el.hidden = !text;
  el.className = ok ? "good" : "bad";
  el.textContent = text ? `${ok ? "✓" : "✗"} ${text}` : "";
}

// Run a YSE PZ request with the button disabled; report its outcome.
async function yseAction(button, request, ok) {
  button.disabled = true;
  yseStatus(true, "");
  try {
    ok(await request());
  } catch (ex) {
    yseStatus(false, ex.message);
  } finally {
    button.disabled = false;
  }
}

async function initYse() {
  const form = $("#yse-form");
  form.onsubmit = (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    yseAction(form.querySelector("button"), () => api("/api/account/yse", data), (r) => {
      showYse(r.username);
      yseStatus(true, `YSE PZ accepted the login for ${r.username}. It's saved; the vetter will use it.`);
    });
  };
  $("#yse-test").onclick = () => yseAction($("#yse-test"), () => api("/api/account/yse/check", {}),
    (r) => yseStatus(true, `YSE PZ still accepts the login for ${r.username}.`));
  $("#yse-change").onclick = () => { $("#yse-on").hidden = true; form.hidden = false; yseStatus(true, ""); };
  $("#yse-forget").onclick = () => yseAction($("#yse-forget"), async () => {
    const r = await fetch("/api/account/yse", { method: "DELETE" });
    if (!r.ok) throw new Error(`couldn't remove it (HTTP ${r.status})`);
  }, () => { showYse(null); yseStatus(true, "Removed. The vetter no longer uses a YSE PZ login for you."); });
  showYse((await api("/api/account/yse")).username);
}

async function init() {
  const cfg = await api("/api/config");
  const token = new URLSearchParams(location.hash.slice(1)).get("reset");

  wire($("#login"), "/api/account/login", home);
  wire($("#signup"), "/api/account/signup", home);
  wire($("#forgot"), "/api/account/reset-request", () => {
    $("#forgot .err").innerHTML = '<span class="done">If that handle and email match a profile, a link is on its way.</span>';
  });
  wire($("#reset"), "/api/account/reset", () => { history.replaceState(null, "", "/account"); home(); }, () => ({ token }));
  $("#forgot-link").onclick = (e) => { e.preventDefault(); show("forgot"); };
  $("#forgot .back").onclick = (e) => { e.preventDefault(); show("guest"); };
  $("#logout").onclick = async () => { await api("/api/account/logout", {}); location.reload(); };

  if (!cfg.accounts) {
    $("#off-labeler").textContent = cfg.labeler;
    show("off", "yse");
    await initYse();
  } else if (token) {
    show("reset");
  } else if (cfg.signed_in) {
    $("#me-handle").textContent = cfg.labeler;
    show("me", "yse");
    await initYse();
  } else {
    show("guest");
  }
}

init().catch((e) => { document.querySelector("main").textContent = e.message; });
