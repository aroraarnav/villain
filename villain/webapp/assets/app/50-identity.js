/* ---- tab 1: session ---- */
function viewSession() {
  const view = $("#view");
  view.innerHTML = `
    <div class="panel">
      <div class="spread"><h2>read a session</h2>
        <span class="small muted">nothing is saved until you ask</span></div>
      <div class="drop" id="drop">
        <div style="font-size:15px;color:var(--ink)">Drop hand history files here</div>
        <div class="small" style="margin-top:4px">or click to choose \u00b7 PokerNow JSON exports</div>
      </div>
      <input type="file" id="file" multiple accept=".json,.txt" hidden>
      <div id="upload-status" class="small muted" style="margin-top:10px"></div>
    </div>
    <div id="session-body"></div>`;

  const drop = $("#drop"), input = $("#file"), status = $("#upload-status");
  if (isGuest()) {
    guestLockDrop(drop);
    if (state.session) renderSession();
    return;
  }
  drop.onclick = () => input.click();
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = e => {
    e.preventDefault(); drop.classList.remove("over");
    handleFiles(e.dataTransfer.files);
  };
  input.onchange = () => handleFiles(input.files);

  async function handleFiles(list) {
    const files = [...list];
    if (!files.length) return;
    status.textContent = `reading ${files.length} file(s)\u2026`;
    try {
      const payload = [];
      for (const f of files) payload.push({name: f.name, content: await f.text()});
      status.textContent = "parsing\u2026";
      const data = await post("/api/upload", {files: payload});
      state.session = data;
      renderSession();
      if (data.questions && data.questions.length && !data.answered) {
        askIdentity(data.token, data.questions, null, data.linked, data.conflicts);
      }
      status.innerHTML = data.rejected && data.rejected.length
        ? `<span class="err">skipped: ${data.rejected.map(r => esc(r.name)).join(", ")}</span>`
        : "";
    } catch (err) {
      status.innerHTML = `<span class="err">${esc(err.message)}</span>`;
    }
  }

  if (state.session) renderSession();
}

function renderSession() {
  const data = state.session, box = $("#session-body");
  if (!box) return;
  box.innerHTML = `
    <div class="panel">
      <div class="spread">
        <div><h2>this session</h2>
          <div class="small muted">${data.hands} hands \u00b7
            ${data.files.map(f => esc(f.name)).join(", ")}</div></div>
        <div class="row">
          <span class="small muted" id="save-note">${data.saved
            ? "saved to database" : "not in the database"}</span>
          <button class="act primary" id="save" ${data.saved ? "disabled" : ""}>
            ${data.saved ? "saved" : "Add to database"}</button>
        </div>
      </div>
      ${data.auto_merged ? `<p class="small muted" style="margin:8px 0 0">
        Linked ${data.auto_merged} known player match(es) automatically
        (kept existing database names).</p>` : ""}
      <div id="session-roster" style="margin-top:12px"></div>
    </div>
    <div id="session-profiles"></div>`;
  $("#session-roster").appendChild(rosterTable(data.players, {onClick: null}));
  playerTabs(data.profiles, $("#session-profiles"));
  const save = $("#save");
  if (save && !data.saved) {
    if (isGuest()) guestLock(save);
    else save.onclick = () => commit(data.token);
  }
}

/* ---- identity, settled at upload ---- */
/* Asked when the file lands rather than when it is saved, so the session you
   are reading has already pooled the accounts. Merging also asks what to call
   the result: choosing silently files a player under a name they have stopped
   using. */
/* Accounts that might be one person arrive as pairs, but three accounts that
   are all the same human arrive as three separate questions -- and nothing
   stops you answering them inconsistently. Union the pairs into components so
   each *person* is one decision. Applying the links is still pairwise, which
   is what makes this safe: the co-occurrence guard is enforced per link, so a
   group can never smuggle through a merge the tool would refuse. */
function sideKey(side) {
  if (!side) return "";
  return side.player_id != null ? `db${side.player_id}` : `ac${side.account}`;
}
function groupQuestions(questions, linked) {
  const parent = {};
  const find = k => { while (parent[k] !== k) k = parent[k] = parent[parent[k]]; return k; };
  const union = (a, b) => {
    parent[a] = parent[a] ?? a; parent[b] = parent[b] ?? b;
    parent[find(a)] = find(b);
  };
  // Settled pairs join clusters even though nobody is asked about them. Two
  // accounts whose names normalise the same are merged without a question, and
  // leaving that edge out split one knot of accounts into two unrelated
  // dialogs -- "ghost"/"ghostly" over here, "Ghosts partner"/"Ghost" over there.
  for (const q of [...questions, ...(linked || [])]) {
    const a = sideKey(q.left), b = sideKey(q.right);
    parent[a] = parent[a] ?? a; parent[b] = parent[b] ?? b;
    union(a, b);
  }
  // Also join accounts whose names match outright. Two accounts both called
  // "tin" are related whether or not they can be merged -- they often cannot,
  // having sat at the table together -- and splitting them across two dialogs
  // asked about half a knot twice and explained neither.
  const byName = new Map();
  for (const q of [...questions, ...(linked || [])]) {
    for (const side of [q.left, q.right]) {
      const key = displayKey(side.name || "");
      if (!key) continue;
      if (byName.has(key)) union(byName.get(key), sideKey(side));
      else byName.set(key, sideKey(side));
    }
  }
  const groups = new Map();
  const at = (root) => {
    if (!groups.has(root)) {
      groups.set(root, {questions: [], members: new Map(), together: []});
    }
    return groups.get(root);
  };
  for (const q of questions) {
    const g = at(find(sideKey(q.left)));
    g.questions.push(q);
    for (const side of [q.left, q.right]) g.members.set(sideKey(side), side);
  }
  for (const q of (linked || [])) {
    const g = at(find(sideKey(q.left)));
    g.together.push([sideKey(q.left), sideKey(q.right)]);
    for (const side of [q.left, q.right]) g.members.set(sideKey(side), side);
  }
  return [...groups.values()];
}

/* Sorting accounts into people.
 *
 * The old control was one radio pair for the whole cluster: merge all six, or
 * keep all six apart. Six accounts are rarely one answer -- a regular with two
 * devices, their brother on a third, and a stranger whose name happens to
 * shorten the same way -- and neither choice was right, so the reader had to
 * pick the less wrong one and repair it afterwards.
 *
 * A column per person, accounts moved between them. The answer the server
 * wants is still pairwise: for every pair it asked about, "same" is simply
 * whether the two ended up in the same column.
 *
 * The starting split is by name, not "everything together": identical names
 * are the reconnect case and belong together, while two different names are
 * two people until somebody says otherwise -- merging being the expensive
 * mistake, and the one that used to need a database reset to undo.
 */
function buildPartition(card, members, together, conflicts) {
  const assigned = new Map();          // account key -> column id
  const columnOf = new Map();          // column id -> chosen display name
  for (const m of members) {
    const key = displayKey(m.name);
    if (!columnOf.has(key)) columnOf.set(key, m.name);
    assigned.set(sideKey(m), key);
  }
  for (const [a, b] of (together || [])) {
    const to = assigned.get(a), from = assigned.get(b);
    if (to === undefined || from === undefined || to === from) continue;
    for (const [key, col] of [...assigned]) if (col === from) assigned.set(key, to);
  }

  // Pairs that can never be one person. Kept as a lookup so a drop can be
  // refused before it happens, rather than accepted and then rejected by the
  // database with a message about hands nobody remembers playing.
  const cannot = new Map();
  for (const [a, b] of (conflicts || [])) {
    if (!assigned.has(a) || !assigned.has(b)) continue;
    if (!cannot.has(a)) cannot.set(a, new Set());
    if (!cannot.has(b)) cannot.set(b, new Set());
    cannot.get(a).add(b);
    cannot.get(b).add(a);
  }
  const blocks = (key, col) => {
    const foes = cannot.get(key);
    if (!foes) return null;
    for (const [other, its] of assigned) {
      if (its === col && foes.has(other)) {
        const who = members.find(m => sideKey(m) === other);
        if (!who) return "another account";
        // The account id when two members share a name, for the same reason.
        const twins = members.filter(x => x.name === who.name).length > 1;
        return twins && who.account ? `${who.name} (${who.account})` : (who.name || who.account);
      }
    }
    return null;
  };
  // Nothing may start in a column it is not allowed to be in.
  for (const m of members) {
    const key = sideKey(m);
    if (blocks(key, assigned.get(key))) assigned.set(key, `solo:${key}`);
  }

  card._partition = assigned;
  card._columnName = columnOf;

  const holder = card.querySelector(".people");
  const note = card.querySelector(".partition-note");
  let picked = null;                   // click-to-place, for touch and keyboard

  const say = (text) => { if (note) note.textContent = text || ""; };

  const place = (key, col) => {
    const why = blocks(key, col);
    if (why) {
      const m = members.find(x => sideKey(x) === key);
      say(`${m ? m.name : "That account"} and ${why} played hands at the same `
          + "table, so they cannot be the same person.");
      return false;
    }
    assigned.set(key, col);
    say("");
    return true;
  };

  const draw = () => {
    const columns = [...new Set(assigned.values())];
    holder.innerHTML = "";
    columns.forEach((col, i) => {
      const wrap = h("div", "person");
      const mine = members.filter(m => assigned.get(sideKey(m)) === col);
      const names = [...new Set(mine.map(m => m.name))];
      const hands = mine.reduce((n, m) => n + (m.hands || 0), 0);
      wrap.innerHTML = `<div class="person-head"><span>Person ${i + 1}</span>
        <span class="small muted">${hands.toLocaleString()} hands</span></div>`;

      for (const m of mine) {
        const key = sideKey(m);
        const chip = h("div", "member" + (picked === key ? " picked" : ""));
        chip.draggable = true;
        chip.tabIndex = 0;
        chip.innerHTML = `<b>${esc(m.name)}</b>
          <span class="small muted">${(m.hands || 0).toLocaleString()} hands`
          + (m.account ? ` · <span class="mono">${esc(String(m.account))}</span>` : "")
          + `</span>`;
        chip.ondragstart = (e) => {
          e.dataTransfer.setData("text/plain", key);
          e.dataTransfer.effectAllowed = "move";
          chip.classList.add("dragging");
        };
        chip.ondragend = () => chip.classList.remove("dragging");
        // Click to pick up, click a column to put down. Drag is faster; this
        // is the one that works on a phone and from a keyboard.
        chip.onclick = (e) => {
          // Without this the click reaches the column underneath, which reads
          // it as "put it here" and drops the card back where it started.
          if (e) e.stopPropagation();
          picked = picked === key ? null : key;
          say("");
          draw();
        };
        chip.onkeydown = (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); chip.onclick(e); }
        };
        wrap.appendChild(chip);
      }

      if (names.length > 1) {
        const pick = h("select", "keepname", names.map(n =>
          `<option value="${esc(n)}" ${n === columnOf.get(col) ? "selected" : ""}>`
          + `keep “${esc(n)}”</option>`).join(""));
        pick.onchange = () => columnOf.set(col, pick.value);
        pick.onclick = (e) => e.stopPropagation();
        wrap.appendChild(pick);
      }
      if (!mine.some(m => m.name === columnOf.get(col))) {
        columnOf.set(col, mine.length ? mine[0].name : columnOf.get(col));
      }

      wrap.onclick = () => { if (picked && place(picked, col)) { picked = null; } draw(); };
      wrap.ondragover = (e) => { e.preventDefault(); wrap.classList.add("over"); };
      wrap.ondragleave = () => wrap.classList.remove("over");
      wrap.ondrop = (e) => {
        e.preventDefault();
        wrap.classList.remove("over");
        const key = e.dataTransfer.getData("text/plain");
        if (key && assigned.get(key) !== col) place(key, col);
        draw();
      };
      holder.appendChild(wrap);
    });

    if (members.length > columns.length) {
      const spare = h("div", "person spare", `<div class="person-head"><span>Someone else</span></div>
        <div class="small muted">${picked ? "click to move here" : "drag here"}</div>`);
      const put = (key) => { assigned.set(key, `solo:${key}`); say(""); };
      spare.onclick = () => { if (picked) { put(picked); picked = null; draw(); } };
      spare.ondragover = (e) => { e.preventDefault(); spare.classList.add("over"); };
      spare.ondragleave = () => spare.classList.remove("over");
      spare.ondrop = (e) => {
        e.preventDefault();
        const key = e.dataTransfer.getData("text/plain");
        if (key) { put(key); draw(); }
      };
      holder.appendChild(spare);
    }
  };

  const all = card.querySelector(".allone");
  if (all) all.onclick = () => {
    // Everything that is allowed to be together, together. Accounts that
    // cannot join keep their own column rather than silently not moving.
    const first = sideKey(members[0]);
    const home = assigned.get(first);
    for (const m of members) {
      const key = sideKey(m);
      if (!blocks(key, home)) assigned.set(key, home);
    }
    const stuck = members.filter(m => assigned.get(sideKey(m)) !== home);
    // Named by account when the names collide: "dev and dev cannot be the same
    // person" is a true sentence that tells the reader nothing about which.
    const label = (m) => {
      const twins = members.filter(x => x.name === m.name).length > 1;
      return twins && m.account ? `${m.name} (${m.account})` : m.name;
    };
    say(stuck.length
      ? `${stuck.map(label).join(" and ")} sat at the table with the rest, `
        + "so cannot be the same person."
      : "");
    picked = null;
    draw();
  };
  const apart = card.querySelector(".allapart");
  if (apart) apart.onclick = () => {
    for (const m of members) assigned.set(sideKey(m), `solo:${sideKey(m)}`);
    for (const [a, b] of (together || [])) {
      if (assigned.has(a) && assigned.has(b)) assigned.set(b, assigned.get(a));
    }
    picked = null;
    say("");
    draw();
  };

  draw();
}

function sideMeta(side) {
  // Only "already in the database" earns a label; everything else in this
  // dialog came from the files being added, so saying so on every row is noise.
  const bits = [];
  if (side.player_id != null) bits.push("in the database");
  bits.push(`${side.hands || 0} hands`);
  const id = side.account ? String(side.account) : "";
  if (id) bits.push(`<span class="mono">${esc(id.length <= 10 ? id
    : `${id.slice(0, 6)}\u2026${id.slice(-3)}`)}</span>`);
  return bits.join(" \u00b7 ");
}

const STAT_LABELS = {
  "vpip": "VPIP", "pfr": "PFR", "three_bet": "3-bet", "limp": "limps",
  "wtsd": "went to showdown", "wsd": "won at showdown",
  "aggression:flop": "flop aggression", "aggression:turn": "turn aggression",
  "aggression:river": "river aggression",
  "fold_vs_bet:flop": "fold vs flop bet", "fold_vs_bet:turn": "fold vs turn bet",
  "fold_vs_bet:river": "fold vs river bet",
};
const SUITS = {s: "\u2660", h: "\u2665", d: "\u2666", c: "\u2663"};
/* A board is the one thing in this tool that is not a statistic, and reading
   "7s Jd 3c" as text is slower than seeing it. */
function cardsEl(list, opts) {
  const wrap = h("span", "cards-row" + ((opts && opts.small) ? " small-cards" : ""));
  for (const raw of (list || [])) {
    const text = String(raw);
    const rank = text.slice(0, -1).replace("T", "10");
    const suit = text.slice(-1).toLowerCase();
    const card = h("span", "card " + (suit === "h" || suit === "d" ? "red" : "black"), `<span class="r">${esc(rank)}</span><span class="s">${
      SUITS[suit] || esc(suit)}</span>`);
    wrap.appendChild(card);
  }
  return wrap;
}

/* "4 limps out of 2,945 chances" is a number; "essentially never limps, which
   is a strong player's habit" is a read. The rate decides which way, the
   glossary supplies the words. */
function evidenceVerdict(d) {
  if (!d.count) return "No hands where this could have happened yet.";
  const rate = d.rate != null ? d.rate : d.hits / d.count;
  const pop = d.population;
  const pct = Math.round(rate * 100);
  const against = d.compared_to || "the field";
  let scale;
  if (d.hits === 0) scale = "never";
  else if (rate < 0.02) scale = "almost never";
  // Without something to compare against, say the rate and stop. Claiming it
  // is "about as often as the field" when no field frequency was involved
  // describes a comparison that never happened.
  else if (pop == null) scale = "";
  else if (rate > pop * 1.35) scale = `far more than ${against}`;
  else if (rate < pop * 0.65) scale = `far less than ${against}`;
  else scale = `about as often as ${against}`;
  const head = scale ? `${d.hits} of ${d.count} \u2014 ${pct}%, ${scale}.`
                     : `${d.hits} of ${d.count} \u2014 ${pct}%.`;
  return d.reading ? `${head} ${d.reading}` : head;
}

function statLabel(stat, rows) {
  const hit = (rows || []).find(r => r.stat === stat);
  return hit ? hit.label : (STAT_LABELS[stat] || stat);
}

function normalizeName(name) {
  const text = String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
  return text.replace(/\d+$/, "") || text;
}

/* The pairwise reason says "both shorten to jay", which is wrong on a group of
   three. Describe the group itself. */
function groupReason(members, questions) {
  const roots = new Set(members.map(m => normalizeName(m.name)));
  const exact = new Set(members.map(m => displayKey(m.name)));
  if (exact.size === 1) {
    return `all ${members.length} appeared as \u201c${members[0].name}\u201d`;
  }
  if (roots.size === 1) {
    return `all ${members.length} shorten to \u201c${[...roots][0]}\u201d`;
  }
  return [...new Set(questions.map(q => q.detail))].join("; ");
}

function displayKey(name) {
  return String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}
function isExactName(q) {
  return displayKey(q.left && q.left.name) === displayKey(q.right && q.right.name);
}

/* When both sides show the same screen name the name tells you nothing, so
   the account id has to be on screen to make the question answerable. */
async function askIdentity(token, questions, onDone, linked, conflicts) {
  const modal = $("#modal");
  modal.innerHTML = `
    <div class="veil"><div class="sheet">
      <h2 style="margin-top:0">Same player?</h2>
      <p class="small muted" style="margin-top:0">
        Same-id accounts were merged already. These are different ids.</p>
      <label class="bulk" id="bulk-wrap" hidden>
        <input type="checkbox" id="merge-exact" checked>
        <span><b>Merge exact name matches</b>
          <span class="small muted" id="bulk-count"></span></span>
      </label>
      <div id="questions"></div>
      <div class="row" style="justify-content:flex-end;margin-top:18px">
        <button class="act" id="cancel">Keep them separate</button>
        <button class="act primary" id="confirm">Apply</button>
      </div>
    </div></div>`;
  const box = $("#questions");
  const groups = groupQuestions(questions, linked);

  for (const g of groups.filter(x => x.questions.length > 1)) {
    const members = [...g.members.values()]
      .sort((a, b) => (b.hands || 0) - (a.hands || 0));
    const allExact = g.questions.every(isExactName);
    const div = h("div", "q group" + (allExact ? " exact" : ""));
    div.dataset.group = g.questions.map(q => q.id).join("|");
    div.innerHTML = `
      <div class="q-prompt">${members.length} accounts with similar names</div>
      <div class="small muted">${esc(groupReason(members, g.questions))}</div>
      <div class="small muted" style="margin-top:6px">One column per person.
        Drag an account to move it, or tap it and then tap a column.</div>
      <div class="row" style="margin-top:8px;gap:10px">
        <button class="act small allone" type="button">All one person</button>
        <button class="act small allapart" type="button">All separate</button>
      </div>
      <div class="people"></div>
      <div class="small err partition-note"></div>`;
    box.appendChild(div);
    buildPartition(div, members, g.together, conflicts);
  }

  const singles = new Set(
    groups.filter(x => x.questions.length === 1).map(x => x.questions[0].id));
  for (const q of questions.filter(x => singles.has(x.id))) {
    const div = h("div", isExactName(q) ? "q exact" : "q");
    // Two identical strings are not a choice, so the picker goes away rather
    // than offering "keep Pratul" or "keep Pratul".
    const distinct = [...new Map((q.names || []).map(n => [displayKey(n), n])).values()];
    const names = distinct.length < 2 ? "" : distinct.map(n => `
      <label><input type="radio" name="name-${esc(q.id)}" value="${esc(n)}"
        ${n === q.default_name ? "checked" : ""}>keep \u201c${esc(n)}\u201d</label>`).join("");
    div.innerHTML = `
      <div class="q-prompt">${esc(q.prompt)}</div>
      <div class="sides">
        <div class="side"><b>${esc(q.left.name)}</b>
          <div class="small muted">${sideMeta(q.left)}</div></div>
        <div class="side"><b>${esc(q.right.name)}</b>
          <div class="small muted">${sideMeta(q.right)}</div></div>
      </div>
      <div class="small muted">${esc(q.detail)}</div>
      <div class="choice">
        <label><input type="radio" name="${esc(q.id)}" value="yes"
          ${q.default ? "checked" : ""}>Same player</label>
        <label><input type="radio" name="${esc(q.id)}" value="no"
          ${q.default ? "" : "checked"}>Different people</label>
      </div>
      ${names ? `<div class="choice namechoice">
        <span class="small muted namelabel">keep the name</span>${names}</div>` : ""}`;
    box.appendChild(div);

    // The name only means anything if they are one person, so it greys out
    // rather than disappearing -- a control that vanishes leaves you wondering
    // whether you missed something.
    const setEnabled = same => {
      const group = $(".namechoice", div);
      if (!group) return;
      group.classList.toggle("disabled", !same);
      group.querySelectorAll("input").forEach(input => { input.disabled = !same; });
    };
    setEnabled(q.default);
    div.querySelectorAll(`input[name="${CSS.escape(q.id)}"]`).forEach(radio =>
      radio.onchange = () => setEnabled(radio.value === "yes"));
  }
  // Exact-name pairs are the bulk of a batch and all get the same answer, so
  // they collapse behind one switch instead of being asked one at a time.
  const exact = questions.filter(isExactName);
  const bulk = $("#merge-exact");
  if (exact.length) {
    $("#bulk-wrap").hidden = false;
    $("#bulk-count").textContent = `\u00b7 ${exact.length} of ${questions.length}`;
    const sync = () => {
      box.querySelectorAll(".q.exact").forEach(el => { el.hidden = bulk.checked; });
    };
    bulk.onchange = sync;
    sync();
  }

  const groupAnswer = (q) => {
    const card = box.querySelector(`.q.group[data-group*="${CSS.escape(q.id)}"]`);
    if (!card || !card._partition) return undefined;
    // The pairwise answer falls straight out of where the two accounts were
    // put: same column, same person.
    const left = card._partition.get(sideKey(q.left));
    const right = card._partition.get(sideKey(q.right));
    if (left === undefined || right === undefined) return undefined;
    const same = left === right;
    return {same,
            name: same ? (card._columnName.get(left) || q.default_name)
                       : q.default_name};
  };

  const answerFor = (q, forceSame) => {
    if (forceSame !== undefined) return {same: forceSame, name: q.default_name};
    const picked = modal.querySelector(`input[name="${CSS.escape(q.id)}"]:checked`);
    const name = modal.querySelector(`input[name="name-${CSS.escape(q.id)}"]:checked`);
    return {same: picked ? picked.value === "yes" : q.default,
            name: name ? name.value : q.default_name};
  };

  const send = async (answers) => {
    showBusy("Applying\u2026", undefined);
    try {
      // The full preview is asked for only when it is on screen. During an
      // import nothing is showing it, and building it costs as much as the
      // import itself.
      const showing = !!(state.session && state.session.token === token);
      const refreshed = await post(
        `/api/session/${token}/identity${showing ? "?full=1" : ""}`, {answers});
      if (showing) {
        state.session = refreshed;
        renderSession();
      }
      if (onDone) await onDone();
      else $("#modal").innerHTML = "";
    } catch (err) {
      // Loudly, and in place. This used to clear the dialog and rethrow to a
      // status line the reader may not have been looking at, so a failed apply
      // looked like an apply that silently did nothing -- with the hands still
      // unsaved and the database still empty.
      const sheet = modal.querySelector(".sheet");
      if (sheet) {
        let note = sheet.querySelector(".apply-err");
        if (!note) {
          note = document.createElement("p");
          note.className = "err apply-err";
          sheet.appendChild(note);
        }
        note.textContent = /expired/i.test(err.message || "")
          ? "This upload timed out while the dialog was open. Nothing was saved — add the files again."
          : `Not applied — ${err.message}`;
      } else {
        $("#modal").innerHTML = "";
      }
      throw err;
    }
  };

  $("#cancel").onclick = async () => {
    // Say "no" explicitly for every question. An empty object meant
    // "unanswered", which falls back to each question's default -- and now that
    // an identical screen name defaults to *merge*, "Keep them separate" was
    // merging the very accounts it promised to leave alone.
    const answers = {};
    for (const q of questions) answers[q.id] = {same: false, name: q.default_name};
    await send(answers);
  };
  $("#confirm").onclick = async () => {
    const answers = {};
    for (const q of questions) {
      const grouped = groupAnswer(q);
      if (grouped) { answers[q.id] = grouped; continue; }
      const forced = (bulk && bulk.checked && isExactName(q)) ? true : undefined;
      answers[q.id] = answerFor(q, forced);
    }
    await send(answers);
  };
}

async function commit(token) {
  try {
    const result = await post(`/api/session/${token}/commit`, {});
    state.session.saved = true;
    renderSession();
    showResult(result);
  } catch (err) {
    showResult({error: err.message});
  }
}

function showResult(result) {
  const modal = $("#modal");
  const body = result.error
    ? `<p class="err">${esc(result.error)}</p>`
    : result.reset
    ? `<p>Removed ${result.reset.hands} hands and ${result.reset.players} players.</p>`
    : `<p>${result.hands_new} new hands stored${result.duplicates
        ? `, ${result.duplicates} already known` : ""}.</p>
       <p class="small muted">${result.players_new} new player(s)\u00b7
         ${result.merged} merge(s) applied.</p>
       ${(result.blocked || []).map(b => `<p class="err small">${esc(b)}</p>`).join("")}`;
  const heading = result.error ? "Could not save" : result.reset ? "Database reset" : "Saved";
  modal.innerHTML = `<div class="veil"><div class="sheet">
    <h2 style="margin-top:0">${heading}</h2>
    ${body}
    <div class="row" style="justify-content:flex-end;margin-top:16px">
      <button class="act" id="close">Close</button>
      ${result.error || result.reset ? "" : '<button class="act primary" id="godb">Open players</button>'}
    </div></div></div>`;
  $("#close").onclick = () => { modal.innerHTML = ""; };
  const go = $("#godb");
  if (go) go.onclick = () => { modal.innerHTML = ""; switchTab("players"); };
}
