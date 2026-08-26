/* ---- tab 2: players ---- */
async function viewPlayers() {
  const view = $("#view");
  if (state.player) return viewPlayer(state.player);
  // Rebuilding the roster from Python is the slow call on this tab. Keep the
  // last one so "all players" can paint immediately instead of looking dead
  // while the same list is computed again. Anything that changes who is in
  // the database drops it.
  let data = state.roster;
  if (!data) {
    data = await get("/api/roster");
    state.roster = data;
  }
  state.heroId = data.hero_id;
  $("#meta").textContent = `${data.hands} hands \u00b7 ${data.players.length} players`;
  if (!data.players.length) {
    if (!onScreen("players")) return;
    view.innerHTML = `<div class="panel"><h2>nothing stored yet</h2>
      <p class="muted">Drop your hand history exports here.</p>
      <div class="drop" id="db-drop">
        <div style="font-size:15px;color:var(--ink)">Drop hand history files here</div>
        <div class="small" style="margin-top:4px">or click to choose \u00b7
          any number at once</div>
      </div>
      <input type="file" id="db-file" multiple accept=".json,.txt" hidden>
      <div id="db-status" class="small muted" style="margin-top:10px"></div></div>`;
    if (isGuest()) guestLockDrop($("#db-drop"));
    else wireImport();
    return;
  }
  /* One table, sorted however you like. A separate leaderboard tab was the
     same rows in a different order. */
  if (!onScreen("players")) return;
  const guest = isGuest();
  view.innerHTML = `<div class="panel">
      <div class="spread"><h2>database</h2>
        <div class="row">
          <span class="small muted" id="db-meta">click a column to re-rank</span>
          <button class="act small nowrap" id="db-add">Add hands</button>
        </div></div>
      <div class="drop compact" id="db-drop" hidden>
        <div style="font-size:14px;color:var(--ink)">Drop hand history files here</div>
        <div class="small" style="margin-top:4px">any number at once</div>
      </div>
      <input type="file" id="db-file" multiple accept=".json,.txt" hidden>
      <div id="db-status" class="small muted" style="margin-bottom:10px"></div>
      <div id="db-roster"></div></div>
    <p class="footnote">
      <button class="linkbtn danger-link" id="reset">reset database</button>
      <span class="muted">deletes every hand, player and merge decision</span></p>`;
  if (guest) {
    guestLock($("#db-add"));
    guestLock($("#reset"));
  } else {
    wireImport();
    $("#reset").onclick = () => confirmReset(data);
  }
  $("#db-roster").appendChild(rosterTable(data.players, {
    onClick: p => {
      if (p.player_id === data.hero_id) { switchTab("hero"); return; }
      state.player = p.player_id; viewPlayer(p.player_id);
    },
    heroId: data.hero_id,
  }));

  // Priors are fitted on import now, not on request. What is worth showing is
  // which population the reads on this page were measured against -- that is
  // the one thing the automatic fit changes about how you should read them.
  const fit = data.fit_priors;
  if (fit && fit.has_fitted) {
    $("#db-meta").innerHTML =
      `measured against your own pool \u00b7 ${fit.players} players`;
  } else if (fit) {
    $("#db-meta").innerHTML =
      `measured against generic online norms \u2014 your pool takes over at 8 players`;
  }


}

async function viewPlayer(id) {
  const view = $("#view");
  const data = await get("/api/player/" + id);
  if (!onScreen("players")) return;
  const names = [...new Set(data.aliases.map(a => a.name))];
  $("#meta").textContent = names.length > 1
    ? `also known as ${names.slice(1).join(", ")}` : "";
  view.innerHTML = "";
  const back = h("p", "", `<button class="linkbtn" id="back">\u2190 all players</button>`);
  view.appendChild(back);
  $("#back").onclick = () => {
    state.player = null;
    if (state.roster) { viewPlayers(); return; }
    renderWithSpinner();
  };

  const holder = document.createElement("div");
  view.appendChild(holder);
  state.heroId = data.hero_id;
  playerTabs(data.profiles, holder, {heroId: data.hero_id});

  // Accounts pooled into this player. A list, not a control surface: the
  // splitting moved into its own dialog behind the actions at the foot of the
  // page, so that a wrong merge and a wrong player are undone from the same
  // place instead of one being a button hidden in a table.
  if (data.aliases && data.aliases.length > 1) {
    const panel = h("div", "panel", `<h2>Accounts</h2>
      <div class="panel-lead">
        ${data.aliases.length} accounts are pooled as this player.</div>
      <div id="alias-rows"></div>`);
    view.appendChild(panel);
    const rows = panel.querySelector("#alias-rows");
    for (const a of data.aliases) {
      const row = h("div", "alias-row", `<span><b>${esc(a.name || a.account)}</b>
          <span class="small muted mono">${esc(a.account)}</span></span>
        <span class="small muted num">${a.hands} hands</span>`);
      rows.appendChild(row);
    }
  }

  if (data.by_table && data.by_table.length > 1) {
    const panel = h("div", "panel", `<h2>split by table size</h2>
      <div class="small muted" style="margin:0 0 12px">
        The read above pools these.</div>
      <div class="scroller"><table><thead><tr>
        <th>table</th><th class="num">hands</th><th>read</th>
        <th class="num">skill</th><th>biggest leak</th>
      </tr></thead><tbody></tbody></table></div>`);
    const body = $("tbody", panel);
    for (const t of data.by_table) {
      const tr = h("tr", "", `<td>${esc(t.regime_label)}</td>
        <td class="num">${t.hands}</td>
        <td>${esc(t.archetype)} <span class="small muted">${fmtPct(t.archetype_confidence)}</span></td>
        <td class="num">${t.skill.score.toFixed(0)}</td>
        <td class="small">${t.leaks.length ? esc(t.leaks[0].headline)
          : '<span class="muted">none</span>'}</td>`);
      body.appendChild(tr);
    }
    view.appendChild(panel);
  }

  const actions = playerActions(data);
  if (actions) view.appendChild(actions);
}

/* The two things you can do to a player rather than with one. Both are
   destructive in different degrees, so both are a dialog rather than a button
   that acts, and both live at the foot of the page rather than beside the read
   -- nothing here is part of playing a hand. */
function playerActions(data) {
  const box = h("div", "panel player-actions", `<h2>Manage this player</h2>
    <div class="panel-lead">Neither of these touches a stored hand. Hands
      belong to a table, not to a person.</div>
    <div class="row"></div>`);
  const row = $(".row", box);

  const split = h("button", "act");
  split.textContent = "Split player\u2026";
  const del = h("button", "act danger");
  del.textContent = "Delete player\u2026";

  if (isGuest()) {
    row.append(guestLock(split), guestLock(del));
    return box;
  }

  const canSplit = (data.aliases || []).length > 1;
  split.disabled = !canSplit;
  if (canSplit) split.onclick = () => splitDialog(data);
  else bindTip(split, `<span class="hl">nothing to split</span><br>
    Only one account is pooled as this player, so there is no second identity
    to separate out.`);

  const isHero = data.hero_id != null && data.player_id === data.hero_id;
  del.disabled = isHero;
  if (isHero) bindTip(del, `<span class="hl">this is you</span><br>
    The Hero tab is built from this identity. Reset the database if you really
    mean to start over.`);
  else del.onclick = () => deleteDialog(data);

  row.append(split, del);
  return box;
}

/* Splitting is per account, not per player, because that is the grain the
   mistake happens at -- one stray account swept into somebody else, not a
   whole identity gone wrong. */
function splitDialog(data) {
  const modal = sheet(`Split ${esc(data.display_name)}`, {body: `
    <div class="panel-lead">These ${data.aliases.length} accounts are pooled as
      one person. Split one out if it is somebody else \u2014 the hands stay
      put, only who they belong to changes, and both profiles are rebuilt from
      them.</div>
    <div id="split-rows"></div>`});
  const rows = $("#split-rows", modal);
  for (const a of data.aliases) {
    const row = h("div", "alias-row", `<span><b>${esc(a.name || a.account)}</b>
        <span class="small muted mono">${esc(a.account)}</span></span>
      <span class="small muted num">${a.hands} hands</span>
      <button class="act small">Split out</button>`);
    const button = $("button", row);
    button.onclick = async () => {
      button.disabled = true;
      button.textContent = "Splitting\u2026";
      try {
        const r = await post("/api/unlink",
          {player_id: data.player_id, site: a.site, account: a.account});
        $("#modal").innerHTML = "";
        // Straight to whoever this just became: the point of splitting is to
        // look at them on their own.
        state.roster = null;
        state.player = r.player_id;
        await viewPlayer(r.player_id);
      } catch (err) {
        button.disabled = false;
        button.textContent = "Split out";
        let msg = row.querySelector(".split-err");
        if (!msg) {
          msg = document.createElement("div");
          msg.className = "small err split-err";
          row.appendChild(msg);
        }
        msg.textContent = err.message || "could not split";
      }
    };
    rows.appendChild(row);
  }
}

/* One click, not a typed phrase. Reset asks you to type the words because it
   costs every hand you have; this costs one identity and leaves the hands
   behind. The veil stays up through the delete *and* the roster rebuild --
   dismissing first left the profile on screen looking frozen. */
function deleteDialog(data) {
  const draw = (err) => {
    sheet(`Delete ${esc(data.display_name)}?`, {bare: true, body: `
      <p>The profile goes. The hands stay.</p>
      <div class="row" style="justify-content:flex-end;margin-top:16px">
        <button class="act" data-close>Cancel</button>
        <button class="act danger" id="do-del">Delete</button>
      </div>
      ${err ? `<div class="small err" style="margin-top:10px">${esc(err)}</div>` : ""}`});
    $("#do-del").onclick = async () => {
      const setBusy = showBusy("Deleting\u2026");
      try {
        await post("/api/player/delete", {player_id: data.player_id});
        setBusy("Opening the roster\u2026", undefined);
        state.player = null;
        state.roster = null;
        await viewPlayers();
        $("#modal").innerHTML = "";
      } catch (e) {
        draw(e.message || "could not delete");
      }
    };
  };
  draw();
}

/* Destructive and irreversible, so it asks for the words rather than a click:
   a stray Enter on a normal dialog should not cost a season of hands. */
function confirmReset(data) {
  const modal = sheet("Reset the database?", {bare: true, body: `
    <p>This deletes <b>${data.hands} hands</b> and
       <b>${data.players.length} profiles</b>, along with every merge and rename
       decision you have made.</p>
    <p class="small muted">The original export files are untouched \u2014 you can
       import them again, but the identity decisions are gone for good.</p>
    <p class="small">Type <b>delete everything</b> to confirm:</p>
    <input id="confirm-text" style="width:100%;padding:8px 10px;border-radius:8px;
      border:1px solid var(--line);background:transparent;color:var(--ink);font:inherit">
    <div class="row" style="justify-content:flex-end;margin-top:16px">
      <button class="act" data-close>Cancel</button>
      <button class="act danger" id="go" disabled>Reset</button>
    </div>`});
  const text = $("#confirm-text", modal), go = $("#go", modal);
  text.focus();
  text.oninput = () => { go.disabled = text.value.trim().toLowerCase() !== "delete everything"; };
  go.onclick = async () => {
    go.disabled = true; go.textContent = "resetting\u2026";
    try {
      const result = await post("/api/reset", {confirm: "delete everything"});
      $("#modal").innerHTML = "";
      state.player = null; state.session = null; state.roster = null;
      viewPlayers();
      paintTabs();               // an emptied database closes tabs again
      showResult({reset: result});
    } catch (err) { showResult({error: err.message}); }
  };
}

/* ---- evidence: the hands behind a number ---- */
async function showEvidence(playerId, stat, headline) {
  // One dialog, three states. The shell was written out for each of them,
  // which is how the close handler ended up wired three times.
  const cls = String(stat).startsWith("vs:") ? "hero-scope" : "";
  const draw = (body) => sheet(esc(headline), {cls, body});
  const open = () => $("#modal").querySelector(".sheet");   // still on screen?
  // Same spinner as the rest of the tool. A muted "finding the hands…" with no
  // motion looked like a hung page on a large sample, which is exactly when
  // this call is slow.
  draw(`<div class="loading"><span class="spinner" aria-hidden="true"></span>
      <span>Finding the hands\u2026</span></div>`);
  let data;
  try {
    data = await get(`/api/evidence?player=${playerId}&stat=${encodeURIComponent(stat)}`);
  } catch (err) {
    if (open()) draw(`<p class="err">${esc(err.message)}</p>`);
    return;
  }
  if (!open()) return;                            // closed while it was working
  const modal = draw(`
    <p class="ev-verdict">${esc(evidenceVerdict(data))}</p>
    <p class="small muted">${data.hits
      ? `Showing the most recent \u2014 click one to replay it.`
      : `Never, in ${data.count} chance${data.count === 1 ? "" : "s"}.`}
      ${data.count > data.hits
      ? `<label class="onlyhits"><input type="checkbox" id="show-all">
           show the ones where it did not</label>` : ""}</p>
    <div id="evlist"></div>`);

  const list = $("#evlist", modal);
  // The hands that moved the number are what you opened this to check; the
  // denominator is one click away, because 19 of 60 and 19 of 20 are different
  // players and hiding that would misrepresent the rate.
  const showAll = $("#show-all");
  if (showAll) {
    showAll.onchange = () => list.querySelectorAll(".ev").forEach(el => {
      el.hidden = !showAll.checked && !el.classList.contains("counted");
    });
  }
  for (const h of data.hands) {
    const row = h("div", "ev");
    const when = h.started_at ? new Date(h.started_at).toLocaleString() : "";
    row.innerHTML = `
      <span class="ev-board"></span>
      <span class="ev-what"><span class="ev-summary">${esc(h.summary)}</span>
        <span class="small muted ev-when">${esc(when)}</span></span>
      <span class="ev-net ${h.net_bb < 0 ? "lost" : ""}">${
        h.net_bb > 0 ? "+" : ""}${h.net_bb} bb</span>`;
    const boardCell = $(".ev-board", row);
    if ((h.board || []).length) boardCell.appendChild(cardsEl(h.board, {small: true}));
    else boardCell.innerHTML = `<span class="small muted">no flop</span>`;
    if ((h.hole_cards || []).length) {
      const hole = cardsEl(h.hole_cards, {small: true});
      hole.classList.add("hole");
      boardCell.appendChild(hole);
    }
    row.classList.toggle("counted", !!h.hit);
    if (!h.hit) row.hidden = true;
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.onkeydown = e => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault(); showReplay(h.hand_id, playerId, headline);
      }
    };
    row.onclick = () => showReplay(h.hand_id, playerId, headline);
    list.appendChild(row);
  }
}

async function showReplay(handId, playerId, headline) {
  // Its own layer: the replay used to append under a list you had scrolled
  // through, so the hand you clicked ended up off screen.
  const layer = sheet("Hand replay", {host: "#modal2", close: "Back", body: `
    ${headline ? `<p class="small muted" style="margin:4px 0 0">${esc(headline)}</p>` : ""}
    <div id="replay"></div>`});
  const box = $("#replay", layer);
  box.appendChild(loadingBlock("Loading the hand\u2026"));
  const r = await get(`/api/hand/${handId}?focus=${playerId}`);
  box.innerHTML = "";
  const seatLine = h("div", "small muted seatline");
  for (const st of r.seats) {
    const chunk = h("span", "seatchunk");
    chunk.textContent = `${st.position} ${st.name} `;
    if (st.hole_cards.length) chunk.appendChild(cardsEl(st.hole_cards, {small: true}));
    seatLine.appendChild(chunk);
  }
  box.innerHTML = `<div class="panel" style="margin-top:14px">
    <div class="small muted">${r.pot_bb} bb pot \u00b7 won by ${esc(r.winners.join(", ") || "\u2014")}</div>
    <div id="seats"></div>
    <div id="streets"></div></div>`;
  $("#seats").appendChild(seatLine);
  const streets = $("#streets");
  for (const st of r.streets) {
    const div = h("div", "street", `<h4>${esc(st.name)}</h4>`);
    if ((st.new_cards || []).length) $("h4", div).appendChild(cardsEl(st.new_cards));
    for (const a of st.actions) {
      const line = h("div", "act" + (a.focus ? " focus" : "") + (a.post ? " post" : ""));
      const amount = a.act.startsWith("check") || a.act.startsWith("fold")
        ? "" : `${a.to_bb} bb`;
      line.innerHTML = `<span class="small muted">${esc(a.position)}</span>
        <span class="who">${esc(a.name)} ${esc(a.act)}</span>
        <span class="amt">${amount}</span>
        <span class="amt small">pot ${a.pot_bb}</span>`;
      div.appendChild(line);
    }
    streets.appendChild(div);
  }

}
