async function readFiles(list) {
  const payload = [];
  for (const f of [...list]) payload.push({name: f.name, content: await f.text()});
  return payload;
}

/* A rebuild the reader did not ask for, reported while it runs.

   A definitions bump makes the next request rebuild every book from every
   stored hand -- about a minute on a real database -- inside whichever
   request happened to arrive first. There is no way to know in advance which
   one that is, so the bar raises itself when the first tick arrives and tears
   itself down when the ticks stop.

   The veil is deliberate, not decoration: it is what stops a second tab from
   being opened mid-migration and starting a second rebuild behind the first.
   Everything is a modal veil until the counters are whole. */
const REBUILD_PHASES = {
  "reading hands": "Reading your stored hands",
  "reading players": "Rebuilding every player's numbers",
};
let rebuildBar = null, rebuildIdle = null, rebuildVeil = null;

window.__villainRebuild = (msg) => {
  if (!rebuildBar) {
    // Commits, links and splits rebuild too, under a veil of their own.
    // Replacing it cut that operation's later steps (the account save) loose
    // from any veil, and clearing it on idle unlocked the window mid-write --
    // so report into the existing veil and leave clearing it to its owner.
    const existing = document.querySelector(".veil.busy");
    rebuildBar = existing ? busyUpdater() : showBusy("Updating your database…");
    rebuildVeil = existing ? null : document.querySelector(".veil.busy");
  }
  const label = REBUILD_PHASES[msg.phase] || "Working";
  const done = Number(msg.done), total = Number(msg.total);
  if (total > 0) {
    rebuildBar(`${label}… ${done.toLocaleString()} of ${total.toLocaleString()}`,
               done / total);
  } else {
    rebuildBar(`${label}…`, undefined);
  }
  // No "finished" tick exists -- the rebuild simply stops calling. A short
  // idle after the last one is what tells us it is over, and it has to be
  // longer than the gap between ticks (~100ms at the slowest phase).
  if (rebuildIdle) clearTimeout(rebuildIdle);
  rebuildIdle = setTimeout(() => {
    if (rebuildVeil && rebuildVeil.isConnected) $("#modal").innerHTML = "";
    rebuildBar = rebuildIdle = rebuildVeil = null;
  }, 1500);
};

/* Import straight into the database: one session for the whole batch, the
   identity questions asked once across all of it, then a single commit. */
function showBusy(text) {
  const modal = $("#modal");
  modal.innerHTML = `<div class="veil busy"><div class="sheet busy-sheet">
    <div class="spinner" aria-hidden="true"></div>
    <div class="busy-body">
      <b id="busy-text"></b>
      <div id="busy-bar" class="busy-bar"><i></i></div>
    </div>
  </div></div>`;
  $("#busy-text").textContent = text;
  return busyUpdater();
}

function busyUpdater() {
  /* (message, fraction).
     A null message leaves the text alone, because progress updates arrive far
     more often than the step they belong to changes.
     A number fills the bar to it. `undefined` leaves the bar running but
     *unmeasured* -- one blocking call into Python that reports nothing back,
     where the only accurate thing to say is "working". A bar that invented a
     percentage there would be the one part of this interface that lies. */
  return (next, fraction) => {
    const el = $("#busy-text");
    if (el && next != null) el.textContent = next;
    const bar = $("#busy-bar");
    if (!bar) return;
    bar.classList.add("on");
    if (fraction == null) {
      // Width is left to the stylesheet: the travelling piece is a fraction of
      // the track, and setting it here would pin it full again.
      bar.classList.add("unmeasured");
      bar.firstChild.style.width = "";
      return;
    }
    bar.classList.remove("unmeasured");
    bar.firstChild.style.width = Math.round(Math.max(0, Math.min(1, fraction)) * 100) + "%";
  };
}

async function importFiles(list, status, done) {
  const files = [...list];
  if (!files.length) return;
  status.textContent = "";
  const setBusy = showBusy(`Reading ${files.length} file(s)\u2026`);
  try {
    const payload = await readFiles(files);
    // Parsed in pieces: in the browser everything runs on this thread, so one
    // parse of two hundred files is a frozen window indistinguishable from a
    // crash. The session is still assembled once and the identity questions
    // asked once. Measured in bytes, not files -- these exports run 3 KB to
    // 2.6 MB, so a file count jumps and stalls by turns.
    const totalBytes = payload.reduce((n, f) => n + (f.content ? f.content.length : 0), 0) || 1;
    const PIECE = 10;
    let token = null, sent = 0;
    for (let i = 0; i < payload.length; i += PIECE) {
      const piece = payload.slice(i, i + PIECE);
      setBusy("Reading hand histories\u2026", sent / totalBytes);
      const step = await post("/api/upload", {files: piece, token, more: true});
      token = step.token;
      sent += piece.reduce((n, f) => n + (f.content ? f.content.length : 0), 0);
      setBusy(null, sent / totalBytes);
    }
    // Closing the batch is its own step and on a large import the longest:
    // every hand deduplicated, every name matched against the database. One
    // call that reports nothing until it returns, so the bar says "working".
    setBusy("Matching players across every file\u2026", undefined);
    const data = await post("/api/upload", {files: [], token, more: false});
    const skipped = (data.rejected || []).length
      ? ` \u00b7 skipped ${data.rejected.map(r => r.name).join(", ")}` : "";
    setBusy(`Parsed ${data.hands} hands\u2026`, undefined);
    const finish = async (answers) => {
      setBusy("Saving and rebuilding profiles\u2026", undefined);
      const r = await post(`/api/session/${data.token}/commit`,
                           answers ? {answers} : {});
      // In the browser the hands are stored *in this tab*, so uploading is
      // part of the same action and happens under the same veil. Absent on
      // the desktop, which has no account to save to.
      if (window.villainSaveNow) {
        setBusy("Saving to your account\u2026", 0);
        try {
          await window.villainSaveNow((fraction) => setBusy(null, fraction));
        } catch (err) {
          $("#modal").innerHTML = "";
          status.innerHTML = `<span class="err">Stored here, but not saved to your `
            + `account: ${esc(err.message)}</span>`;
          if (done) done(status.innerHTML);
          return;
        }
      }
      $("#modal").innerHTML = "";
      // Inline, not a modal: after a batch you want to be looking at the
      // roster you just changed, not dismissing a box in front of it.
      const bits = [`${r.hands_new} new hand(s) stored`];
      if (r.duplicates) bits.push(`${r.duplicates} already known`);
      if (r.unusable) bits.push(`${r.unusable} unreadable`);
      if (r.players_new) bits.push(`${r.players_new} new player(s)`);
      if (r.merged) bits.push(`${r.merged} merge(s)`);
      if (r.priors_fitted) {
        bits.push(`priors refitted from ${r.priors_fitted.players} players`);
      }
      status.innerHTML = esc(bits.join(" \u00b7 ")) + esc(skipped) +
        (r.blocked || []).map(b => `<div class="err">${esc(b)}</div>`).join("");
      if (done) done(status.innerHTML);
    };
    if (data.questions && data.questions.length && !data.answered) {
      $("#modal").innerHTML = "";
      askIdentity(data.token, data.questions, finish, data.linked, data.conflicts);
    } else {
      await finish(null);
    }
  } catch (err) {
    $("#modal").innerHTML = "";
    status.innerHTML = `<span class="err">${esc(err.message)}</span>`;
  }
}

/* Binds whichever import controls are on the page. Both states of the
   Database tab share one handler so they cannot drift apart. */
function wireImport() {
  const input = $("#db-file"), status = $("#db-status"), drop = $("#db-drop");
  if (!input || !status) return;
  const go = (files) => importFiles(files, status, async (summary) => {
    state.player = null;
    state.roster = null;
    await viewPlayers();
    // Hands just arrived: Hero and Simulate may have become possible.
    paintTabs();
    const after = $("#db-status");
    if (after && summary) after.innerHTML = summary;
  });
  wireDrop(drop, input, go);
  const button = $("#db-add");
  if (button && drop) {
    button.onclick = () => { drop.hidden = false; input.click(); };
  }
  // Dropping anywhere on the panel works too: hunting for a target is friction
  // on the one action this tab exists for.
  const panel = status.closest(".panel");
  if (panel && drop) {
    panel.ondragover = e => { e.preventDefault(); drop.hidden = false; };
    panel.ondrop = e => {
      e.preventDefault();
      go(e.dataTransfer.files);
    };
  }
}

/* ---- sittings, derived from the database ---- */
function whenLabel(ms, withTime) {
  if (!ms) return "";
  const d = new Date(ms);
  // The year matters: these sittings span months, so "Aug 12" and "Oct 29"
  // are ambiguous without it.
  const thisYear = d.getFullYear() === new Date().getFullYear();
  const day = d.toLocaleDateString([], {weekday: "short", day: "numeric",
    month: "short", ...(thisYear ? {} : {year: "numeric"})});
  return withTime ? `${day} \u00b7 ${d.toLocaleTimeString([],
    {hour: "2-digit", minute: "2-digit"})}` : day;
}

async function viewSessions() {
  const view = $("#view");
  // No loading panel here: switchTab has already painted the spinner, and a
  // second, plainer one underneath it only made the wait look like two waits.
  const sessions = await get("/api/sessions");
  if (!sessions.length) {
    if (!onScreen("sessions")) return;
    view.innerHTML = `<div class="panel"><h2>no sittings yet</h2>
      <p class="muted">Add hand histories on the Database tab.</p></div>`;
    return;
  }
  // No sitting named, or one named by a link that no longer exists: open the
  // latest, and say so in the address bar without adding a step to go back to.
  if (!sessions.some(x => x.id === state.sessionId)) {
    state.sessionId = sessions[0].id;
    if (onScreen("sessions")) syncUrl(true);
  }
  // The list lives beside the detail, not above it: twenty sittings pushed the
  // thing you came to read off the bottom of the screen, and switching meant
  // scrolling back up every time.
  if (!onScreen("sessions")) return;
  view.innerHTML = `<div class="sess-layout${state.sessListHidden ? " collapsed" : ""}"
      id="sess-layout">
      <div class="panel sess-list">
        <div class="spread"><h2 style="margin:0">sittings</h2>
          <button class="iconbtn" id="sess-toggle"
            title="hide the list">\u00ab</button></div>
        <div id="sess-rows"></div>
      </div>
      <div class="sess-main">
        <h2 id="sess-title" class="sess-title">who played, and how</h2>
        <div id="sess-body" class="sess-body"></div>
      </div>
    </div>`;
  const rows = $("#sess-rows");
  for (const sess of sessions) {
    const item = h("button", "sess-item" + (sess.id === state.sessionId ? " on" : ""));
    const hrs = Math.floor(sess.minutes / 60), mins = sess.minutes % 60;
    item.innerHTML = `<span class="sess-when">${esc(whenLabel(sess.started_at, true))}</span>
      <span class="small muted">${hrs ? hrs + "h " : ""}${mins}m \u00b7 ${
        sess.hands} hands \u00b7 ${sess.players}p</span>`;
    item.onclick = () => { state.sessionId = sess.id; syncUrl(); viewSessions(); };
    rows.appendChild(item);
  }
  const layout = $("#sess-layout"), toggle = $("#sess-toggle");
  toggle.onclick = () => {
    state.sessListHidden = !state.sessListHidden;
    layout.classList.toggle("collapsed", state.sessListHidden);
    toggle.textContent = state.sessListHidden ? "\u00bb" : "\u00ab";
    toggle.title = state.sessListHidden ? "show the list" : "hide the list";
  };
  if (state.sessListHidden) { toggle.textContent = "\u00bb"; }
  const chosen = sessions.find(x => x.id === state.sessionId);
  if (chosen) {
    $("#sess-title").textContent =
      `${whenLabel(chosen.started_at, true)} \u00b7 ${chosen.hands} hands`;
  }
  await drawSession(state.sessionId);
}
