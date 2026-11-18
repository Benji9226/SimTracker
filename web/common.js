// Shared header for every SimTracker page: nav, session picker, and the car / track line.
const $ = id => document.getElementById(id);

async function api(path) {
  const r = await fetch(path); const j = await r.json();
  if (j.error) throw new Error(j.error);
  return j;
}

const App = {
  file: null,          // the session that is on screen (the last one that opened successfully)
  pending: null,       // a session that could not be opened yet (the game still has its file locked); retried
  sessions: [], labels: {}, known: "", mtimes: {},

  // page: "laps" | "turns".  onSession(file, refreshOnly) loads a session into the page. It must return false
  // when the session could not be opened, and in that case must leave what is on screen alone.
  start(page, onSession) {
    document.querySelector("header").innerHTML = `
      <div class="bar">
        <h1>SimTracker</h1>
        <nav><a href="/" class="${page === "laps" ? "active" : ""}">Compare laps</a>
             <a href="/turns" class="${page === "turns" ? "active" : ""}">What to practise</a></nav>
        <select id="sessions"></select>
        <span class="pill" id="status"></span>
      </div>
      <div id="notice" class="notice" hidden></div>
      <div id="carline"></div>`;
    if (!$("appFooter")) document.body.insertAdjacentHTML("beforeend", '<footer id="appFooter"></footer>');
    api("/api/status").then(s => {
      const last = s.last ? ` Last check ${new Date(s.last.at).toLocaleString()}: ${s.last.deleted ? `deleted ${s.last.deleted} file${s.last.deleted === 1 ? "" : "s"} (${s.last.freed_mb} MB)` : "nothing to delete"}${s.last.kept ? `, ${s.last.kept} left alone` : ""}.` : "";
      $("appFooter").textContent = (s.cleanup_days > 0
        ? `Old telemetry is cleaned up automatically: session files not written to for ${s.cleanup_days} days are deleted (your saved laps are kept).`
        : "Automatic telemetry clean-up is off.") + ` Telemetry folder: ${s.folder_files} files, ${s.folder_mb} MB.` + last;
    }).catch(() => {});
    $("sessions").onchange = e => App.choose(e.target.value, onSession);
    const poll = async () => {
      const list = await api("/api/sessions");
      App.sessions = list;
      App.labels = Object.fromEntries(list.map(s => [s.name, s.label || s.name]));
      if (App.pending && !list.some(s => s.name === App.pending)) App.pending = null;
      if (App.pending) {                                   // the new session could not be opened last time: try again
        if (await App.show(App.pending, onSession)) $("sessions").value = App.file;
        App.notice();
      }
      const sig = list.map(s => s.name + s.mtime).join("|");
      if (sig === App.known) return;
      const first = App.known === "";
      App.known = sig;
      const sel = $("sessions");
      sel.innerHTML = list.map(s => `<option value="${s.name}">${s.label || s.name}</option>`).join("");
      if (!list.length) { if ($("msg")) $("msg").textContent = "No sessions found."; return; }
      sel.value = App.file || list[0].name;
      let want = App.file;
      if (first) { try { want = sessionStorage.getItem("simtracker.file"); } catch (e) {} }
      if (!list.some(s => s.name === want)) want = list[0].name;
      // A new session was saved while you were looking at the latest one: follow it.
      if (!first && App.newest && want === App.file && App.file === App.newest && list[0].name !== App.newest) want = list[0].name;
      App.newest = list[0].name;
      const changed = App.mtimes[want] !== undefined && App.mtimes[want] !== list.find(s => s.name === want).mtime;
      list.forEach(s => App.mtimes[s.name] = s.mtime);
      if (first || want !== App.file) await App.choose(want, onSession);
      else if (changed) await onSession(want, true);       // the session on screen grew: refresh it (if it can't be read right now, the old view stays)
    };
    poll().catch(e => App.status("Error: " + e.message));
    setInterval(() => poll().catch(() => {}), 5000);
  },

  // Load a session into the page. Returns true when it opened; only then does it replace what is on screen.
  async show(file, onSession) {
    let ok = false;
    try { ok = (await onSession(file, false)) !== false; } catch (e) { ok = false; }
    if (!ok) return false;
    App.file = file;
    if (App.pending === file) App.pending = null;
    try { sessionStorage.setItem("simtracker.file", file); } catch (e) {}
    App.carline(file);
    return true;
  },

  async choose(file, onSession) {
    const sel = $("sessions");
    sel.value = file;
    if (await App.show(file, onSession)) { App.notice(); return; }
    App.pending = file;                                    // keep trying; meanwhile show the last session that worked
    if (App.file) {
      sel.value = App.file;
    } else {                                               // nothing on screen yet (e.g. the page was just opened)
      for (const s of App.sessions) {
        if (s.name !== file && await App.show(s.name, onSession)) { sel.value = s.name; break; }
      }
    }
    App.notice();
  },

  notice() {
    const box = $("notice");
    if (!box) return;
    if (!App.pending) { box.hidden = true; return; }
    const wanted = App.labels[App.pending] || "the new session";
    const shown = App.file ? (App.labels[App.file] || "your previous session") : null;
    box.textContent = `${wanted} can't be opened yet: the game is probably still setting it up, so the file is locked. ` +
      (shown ? `Showing ${shown} for now. ` : "") + "This page checks again every few seconds and switches over by itself.";
    box.hidden = false;
  },

  async carline(file) {
    try {
      const { info } = await api("/api/info?file=" + encodeURIComponent(file));
      const esc = s => String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
      $("carline").innerHTML =
        `<span class="car">${esc(info.car_model || info.car || "Unknown car")}</span>` +
        (info.car_class ? `<span class="badge">${esc(info.car_class)}</span>` : "") +
        `<span class="track">${esc(info.track || "")}${info.layout && info.layout !== info.track ? " – " + esc(info.layout) : ""}</span>` +
        `<span class="pill">${esc([info.session_type, info.recorded].filter(Boolean).join(" · "))}</span>`;
    } catch (e) { /* keep the line that is there */ }
  },

  status(t) { if ($("status")) $("status").textContent = t; },
};

// POST some JSON (save a lap, rename a turn ...). Throws the server's error message.
async function apiPost(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json();
  if (j.error) throw new Error(j.error);
  return j;
}

// Click a turn name to rename it. Names are saved with the track, so they stay for every session.
async function renameTurn(number, current, after) {
  const name = prompt("Name for this turn (leave empty to go back to the default):", current);
  if (name === null) return;
  try { await apiPost("/api/turns/rename", { file: App.file, number, name }); await after(); }
  catch (e) { alert("Could not rename: " + e.message); }
}

const shortDate = d => { const t = new Date(String(d).slice(0, 10)); return isNaN(t) ? "" : t.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }); };

// Plotly's hover box is white with grey text by default. This gives it the page's own colours (dark or light).
function hoverStyle() {
  const v = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  return { bgcolor: v("--panel"), bordercolor: v("--line"), font: { color: v("--text"), size: 13 }, namelength: -1 };   // namelength -1: never cut the lap name short
}
