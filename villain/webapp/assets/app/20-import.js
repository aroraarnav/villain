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
let rebuildBar = null, rebuildIdle = null;

window.__villainRebuild = (msg) => {
  if (!rebuildBar) {
    rebuildBar = showBusy("Updating your database…");
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
    rebuildBar = rebuildIdle = null;
    const modal = $("#modal");
    if (modal) modal.innerHTML = "";
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
    // Parsed in pieces rather than in one call. In the browser the whole tool
    // runs on this thread, so a single parse of two hundred files is a frozen
    // window with no way to tell it apart from a crash; the same work in
    // tens is a counter that moves. The session is assembled server-side and
    // the identity questions are still asked once, over the whole batch.
    // Measured in bytes rather than in files. A run of these exports goes from
    // 3 KB to 2.6 MB, so counting files makes the bar jump and stall by turns;
    // counting bytes tracks the work actually being done.
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
    // Closing the batch is its own step, and on a large import much the
    // longest: every hand is deduplicated and every name in it matched against
    // everybody already in the database. One call into Python that reports
    // nothing until it returns, so the bar says "working" rather than guessing.
    setBusy("Matching players across every file\u2026", undefined);
    const data = await post("/api/upload", {files: [], token, more: false});
    const skipped = (data.rejected || []).length
      ? ` \u00b7 skipped ${data.rejected.map(r => r.name).join(", ")}` : "";
    setBusy(`Parsed ${data.hands} hands\u2026`, undefined);
    const finish = async (answers) => {
      setBusy("Saving and rebuilding profiles\u2026", undefined);
      const r = await post(`/api/session/${data.token}/commit`,
                           answers ? {answers} : {});
      // In the browser the import is not finished when the hands are stored --
      // they are stored in this tab. Uploading is part of the same action, so
      // it happens under the same veil rather than silently afterwards.
      // Absent on the desktop, which has no account to save to.
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
  input.onchange = () => go(input.files);
  const button = $("#db-add");
  if (button && drop) {
    button.onclick = () => { drop.hidden = false; input.click(); };
  }
  if (drop) {
    drop.onclick = () => input.click();
    drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = e => {
      e.preventDefault(); drop.classList.remove("over");
      go(e.dataTransfer.files);
    };
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
  if (state.sessionId == null) state.sessionId = sessions[0].id;
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
      <div class="panel sess-main">
        <h2 id="sess-title">who played, and how</h2>
        <div id="sess-body"></div>
      </div>
    </div>`;
  const rows = $("#sess-rows");
  for (const sess of sessions) {
    const item = h("button", "sess-item" + (sess.id === state.sessionId ? " on" : ""));
    const hrs = Math.floor(sess.minutes / 60), mins = sess.minutes % 60;
    item.innerHTML = `<span class="sess-when">${esc(whenLabel(sess.started_at, true))}</span>
      <span class="small muted">${hrs ? hrs + "h " : ""}${mins}m \u00b7 ${
        sess.hands} hands \u00b7 ${sess.players}p</span>`;
    item.onclick = () => { state.sessionId = sess.id; viewSessions(); };
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

async function drawSession(id) {
  const body = $("#sess-body");
  body.innerHTML = "";
  body.appendChild(loadingBlock("Reading the sitting\u2026"));
  const data = await get(`/api/session-detail?id=${id}`);
  body.innerHTML = "";
  for (const p of data.players) {
    const div = h("div", "sess-row" + (p.is_hero ? " hero-scope hero-sitting" : ""));
    const netTxt = p.net_bb > 0 ? `+${p.net_bb}` : `${p.net_bb}`;
    // Net bb and skill are the only measurements on this page, and they were
    // 12.5px muted text at the right margin. Same stat-pair the profile header
    // uses -- one figure treatment across the app, not a per-screen decision.
    div.innerHTML = `<div class="sess-head">
        <div class="sess-id">
          <div class="sess-who"><b class="linkish">${esc(p.name)}</b>${
            p.is_hero ? '<span class="tag hero-tag">you</span>' : ""}
            <span class="tag arch ${p.confidence >= 0.5 ? "on" : ""}">${esc(p.archetype)}</span>
            <span class="sitting-note">this sitting</span>
          </div>
          <div class="small muted">${p.hands} hands \u00b7 ${esc(p.regime_label || "")}</div>
        </div>
        <div class="sess-stats">
          <div class="stat-pair">
            <span class="v ${p.net_bb >= 0 ? "up" : "down"}">${netTxt}</span>
            <span class="k">bb</span>
          </div>
          <div class="stat-pair sess-skill">
            <span class="v">${Math.round(p.skill)}</span>
            <span class="k">skill</span>
          </div>
        </div>
      </div>
      <div class="sess-deltas"></div>`;
    $("b", div).onclick = () => switchTab("players", p.player_id);
    const skillBar = bar(p.skill, 100, "var(--mark-2)", 999);
    skillBar.setAttribute("preserveAspectRatio", "none");
    $(".sess-skill", div).appendChild(skillBar);
    // This pill is a sitting-only read, not the pooled one on their Database
    // page -- Database and Sessions disagreeing about the same person is
    // correct (a sitting can look nothing like the season), but only if it
    // says so rather than looking like the same claim twice.
    $(".sitting-note", div).appendChild(info(`<span class="hl">this sitting</span><br>
      Measured on just tonight's hands here, not the pooled read on their
      Database page. The two can disagree, and when they do the difference is
      the point.`));
    const box = $(".sess-deltas", div);
    if (!p.deltas.length) {
      // One line on the row, not a paragraph per player. Six of these stacked
      // was the whole page on a database with no outside sample yet.
      box.innerHTML = `<div class="small muted sess-none">No outside sample at
        this table size to compare tonight against.</div>`;
    } else {
      // One table size at a time, picked with a tab, rather than every table
      // size stacked under its own heading. Rendering a row per (stat,
      // regime) put VPIP on screen two or three times with the table size in
      // small print, which read as a duplicate rather than as two different
      // games -- stacked headings fixed the duplication but still made you
      // scroll past every table size to find the one you sat at. A player
      // who only played one table size gets no tabs at all.
      const byRegime = new Map();
      for (const d of p.deltas) {
        const key = d.regime_label || d.regime || "";
        if (!byRegime.has(key)) byRegime.set(key, []);
        byRegime.get(key).push(d);
      }
      const regimes = [...byRegime.keys()];
      const rows = document.createElement("div");
      // Tonight against usually, as two bars on one scale -- the same picture
      // the against-you panel draws, for the same reason: the finding is the
      // gap, and a four-column text grid makes the reader do the subtraction
      // that a pair of bars does for them.
      const drawRows = label => {
        rows.innerHTML = "";
        const set = byRegime.get(label);
        const max = Math.max(...set.flatMap(d => [d.session, d.usual]), 0.01);
        for (const d of set) {
          const row = h("div", "sess-delta");
          const up = d.delta > 0;
          row.innerHTML = `<div class="sess-delta-head">
              <span class="sess-stat">${esc(statLabel(d.stat, null))}</span>
              <span class="small ${up ? "up" : "down"}">${up ? "\u25b2" : "\u25bc"}${
                Math.abs(Math.round(d.delta * 100))}pp</span>
            </div>`;
          for (const [name, v, color] of [["tonight", d.session, "var(--mark-3)"],
                                          ["usually", d.usual, "var(--mark-1)"]]) {
            const line = h("div", "metric");
            const label2 = h("span", "small muted"); label2.textContent = name;
            const val = h("span", "small tellval"); val.textContent = fmtPct(v);
            const drawn = bar(v, max, color, 150);
            drawn.setAttribute("preserveAspectRatio", "none");
            line.append(label2, drawn, val);
            row.appendChild(line);
          }
          bindTip($(".sess-stat", row), statTip(d.stat, statLabel(d.stat, null)));
          rows.appendChild(row);
        }
      };
      if (regimes.length > 1) {
        const tabs = h("div", "sess-regime-tabs");
        regimes.forEach((label, i) => {
          const b = h("button", "sess-regime-tab" + (i === 0 ? " on" : ""));
          b.textContent = label;
          b.onclick = () => {
            tabs.querySelectorAll(".sess-regime-tab").forEach(x => x.classList.remove("on"));
            b.classList.add("on");
            drawRows(label);
          };
          tabs.appendChild(b);
        });
        box.appendChild(tabs);
      }
      box.appendChild(rows);
      drawRows(regimes[0]);
    }
    body.appendChild(div);
  }
}
