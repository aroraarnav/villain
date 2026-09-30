/* ---- one sitting, reviewed ----
   The order is the order a coach would talk you through it: what to fix
   first, the pots that decided the night, then the evidence behind each fix
   (preflop by spot, 3-bets, folds by bet size), whether any of it is new,
   and last the table -- how each opponent played tonight and how to beat
   them. Every claim cites hands, and every hand opens in the replay. */

const sgn = v => `${v > 0 ? "+" : ""}${v}`;

async function drawSession(id) {
  const body = $("#sess-body");
  body.innerHTML = "";
  body.appendChild(loadingBlock("Reading the sitting…"));
  let data;
  try { data = await get(`/api/session-detail?id=${id}`); }
  catch (err) { body.innerHTML = `<p class="err">${esc(err.message)}</p>`; return; }
  body.innerHTML = "";
  body.appendChild(sittingHead(data));
  if (data.hero_seated) {
    body.appendChild(fixFirst(data));
  }
  body.appendChild(keyHandsPanel(data));
  if (data.hero_seated) {
    body.appendChild(preflopPanel(data));
    body.appendChild(threeBetPanel(data));
    body.appendChild(foldSizePanel(data));
    body.appendChild(trendPanel(data));
  }
  body.appendChild(tablePanel(data));
}

/* A hand, as a control. `ref` carries a hand id and whatever short label the
   finding attached: a hand class for preflop, a street and size for a fold. */
function refChips(refs, heroId) {
  const wrap = h("span", "hand-chips");
  // Preflop lists repeat hand classes: "43o 43o 43o" is one finding three
  // times. Grouped, the chip opens the most recent of them.
  const seen = new Map();
  for (const r of refs || []) {
    if (!r.cls) { seen.set(Symbol(), {ref: r, n: 1}); continue; }
    const hit = seen.get(r.cls);
    if (hit) hit.n += 1; else seen.set(r.cls, {ref: r, n: 1});
  }
  for (const {ref: r, n} of seen.values()) {
    const b = h("button", "hand-chip");
    b.textContent = (r.cls || r.note || "hand") + (n > 1 ? ` \u00d7${n}` : "");
    b.title = r.note ? `open this hand: ${r.note}` : "open this hand";
    b.onclick = () => showReplay(r.hand_id, heroId, r.note || r.cls || "");
    wrap.appendChild(b);
  }
  return wrap;
}

function sittingHead(data) {
  const me = (data.players || []).find(p => p.is_hero);
  const stakes = (data.stakes || []).map(s => {
    const f = v => s.unit === "cents" ? `$${(v / 100).toFixed(2)}` : `${v}`;
    return `${f(s.sb)}/${f(s.bb)}${data.stakes.length > 1 ? ` (${s.hands} hands)` : ""}`;
  }).join(", ");
  const head = h("div", "sr-head", `
    <div class="small muted">${esc(stakes)} · ${data.players.length} players</div>
    <div class="row sr-actions">
      ${me ? `<span class="stat-pair"><span class="v ${me.net_bb >= 0 ? "up" : "down"}">${
        sgn(me.net_bb)}</span><span class="k">your bb</span></span>` : ""}
      <button class="act small" id="sr-export">Copy for Claude</button>
    </div>`);
  $("#sr-export", head).onclick = () => exportSheet(data.id);
  return head;
}

async function exportSheet(id) {
  const card = sheet("Copy for Claude", {body: `
    <p class="small muted">The whole sitting as plain text, amounts in big
      blinds, your cards shown. Paste it into a conversation to ask about a
      hand. Nothing leaves this page unless you paste it somewhere.</p>
    <textarea id="sr-text" class="sr-text" readonly></textarea>
    <div class="row" style="justify-content:flex-end;margin-top:12px">
      <span class="small muted" id="sr-copied"></span>
      <button class="act primary" id="sr-copy">Copy</button>
    </div>`});
  const area = $("#sr-text", card);
  area.value = "Loading…";
  try { area.value = (await get(`/api/session-export?id=${id}`)).text; }
  catch (err) { area.value = err.message; return; }
  $("#sr-copy", card).onclick = async () => {
    try { await navigator.clipboard.writeText(area.value); }
    catch { area.select(); document.execCommand("copy"); }
    $("#sr-copied", card).textContent = "copied";
  };
}

/* ---- what to fix first ---- */
const FIX_SHOWN = 3;

function fixFirst(data) {
  const panel = h("div", "panel sr-fixes", `<h2 id="sr-fix-head">fix first</h2>`);
  $("#sr-fix-head", panel).appendChild(info(termTip("fix first")));
  const fixes = data.fixes || [];
  if (!fixes.length) {
    panel.appendChild(h("div", "small muted",
      "Nothing in this sitting clears the bar for a fix. That is a result, not a gap."));
    return panel;
  }
  const list = h("div", "sr-fix-list");
  fixes.forEach((f, i) => {
    const row = h("div", `review-tip stop sr-fix${i >= FIX_SHOWN ? " extra" : ""}`, `
      <div class="sr-fix-title"><span class="sr-rank">${i + 1}</span><b></b></div>
      <div class="leak-advice small"></div>`);
    $("b", row).textContent = f.title;
    $(".leak-advice", row).textContent = f.detail;
    row.appendChild(refChips(f.hands, data.hero_id));
    list.appendChild(row);
  });
  panel.appendChild(list);
  if (fixes.length > FIX_SHOWN) {
    const more = h("button", "linkbtn");
    more.textContent = `Show all ${fixes.length}`;
    more.onclick = () => {
      const open = list.classList.toggle("all");
      more.textContent = open ? "Show fewer" : `Show all ${fixes.length}`;
    };
    panel.appendChild(more);
  }
  return panel;
}

/* ---- the pots that decided it ---- */
function keyHandsPanel(data) {
  const panel = h("div", "panel", `<h2>the biggest pots</h2>
    <div class="panel-lead">All-in equity splits each result into what the
      decision was worth and what the cards did. Click a pot to replay it.</div>`);
  for (const k of data.key_hands || []) {
    const row = h("div", "sr-pot", `
      <div class="sr-pot-top">
        <span class="mono sr-pot-size">${k.pot_bb}bb</span>
        <span class="sr-pot-board"></span>
        <span class="sr-pot-who"></span>
      </div>
      <div class="sr-pot-notes"></div>`);
    if (k.board.length) $(".sr-pot-board", row).appendChild(cardsEl(k.board, {small: true}));
    else $(".sr-pot-board", row).innerHTML = `<span class="small muted">no flop</span>`;
    for (const p of k.players) {
      const who = h("span", "sr-seat" + (p.is_hero ? " hero-scope" : ""), `
        <span class="small">${esc(p.name)}</span><span class="sr-seat-cards"></span>
        <span class="mono small ${p.net_bb >= 0 ? "up" : "down"}">${sgn(p.net_bb)}</span>`);
      if (p.cards.length) $(".sr-seat-cards", who).appendChild(cardsEl(p.cards, {small: true}));
      $(".sr-pot-who", row).appendChild(who);
    }
    const notes = $(".sr-pot-notes", row);
    if (k.allin) {
      const a = k.allin;
      const line = h("div", "small muted", `All-in with ${fmtPct(a.equity)} equity:
        worth ${sgn(a.expected_bb)}bb, luck ${sgn(a.luck_bb)}bb.`);
      if (a.cooler) line.insertAdjacentHTML("afterbegin", `<span class="tag">cooler</span> `);
      line.appendChild(info(termTip("all-in equity")));
      notes.appendChild(line);
    }
    for (const f of k.flags || []) {
      const flag = h("div", "review-tip stop small");
      flag.textContent = f.text;
      notes.appendChild(flag);
    }
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    const open = () => showReplay(k.hand_id, data.hero_id, `${k.pot_bb}bb pot`);
    row.onclick = e => { if (!e.target.closest(".info")) open(); };
    row.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } };
    panel.appendChild(row);
  }
  return panel;
}

/* A rate against its reference, drawn as one bar and one tick: the finding is
   the distance between them, and two numbers side by side make the reader
   subtract. */
function vsBar(rate, target, off) {
  const wrap = h("span", "sr-vs");
  wrap.innerHTML = `<span class="sr-vs-fill${off ? " off" : ""}"
      style="width:${Math.min(100, 100 * rate)}%"></span>
    <span class="sr-vs-mark" style="left:${Math.min(100, 100 * target)}%"></span>`;
  return wrap;
}

/* ---- preflop, spot by spot ---- */
function preflopPanel(data) {
  const panel = h("div", "panel", `<h2 id="sr-pf-head">your preflop, by spot</h2>
    <div class="panel-lead">Bar: how often you played the hand. Tick: the
      reference range. Chips: the hands on the wrong side of it.</div>`);
  $("#sr-pf-head", panel).appendChild(info(termTip("reference range")));
  for (const s of data.preflop || []) {
    const row = h("div", "sr-spot" + (s.flagged ? " flagged" : ""), `
      <div class="sr-spot-top">
        <span class="sr-spot-label"></span>
        <span class="small muted sr-spot-n">${fmtPct(s.rate)} of ${s.n} · ref ~${fmtPct(s.target)}</span>
      </div>
      <div class="sr-spot-bar"></div>
      <div class="sr-spot-hands"></div>`);
    $(".sr-spot-label", row).textContent = s.label;
    $(".sr-spot-bar", row).appendChild(vsBar(s.rate, s.target, s.flagged));
    const hands = $(".sr-spot-hands", row);
    for (const [list, count, word, term] of [[s.misfolds, s.misfold_count, "folded", "misfold"],
                                             [s.loose, s.loose_count, "played", "loose play"]]) {
      if (!list.length) continue;
      // Over a whole history the list is capped to the latest hands; the
      // count says how many there are in all.
      const more = count > list.length ? ` ${count}, latest ${list.length}:` : "";
      const line = h("div", "sr-spot-line", `<span class="small muted">${word}${more}</span>`);
      $("span", line).appendChild(info(termTip(term)));
      line.appendChild(refChips(list, data.hero_id));
      hands.appendChild(line);
    }
    panel.appendChild(row);
  }
  return panel;
}

/* ---- 3-bets ---- */
function threeBetPanel(data) {
  const t = data.three_bets;
  const panel = h("div", "panel", `<h2>3-bets</h2>`);
  if (!t || !t.faced_opens) {
    panel.appendChild(h("div", "small muted", "You never faced a single raise this sitting."));
    return panel;
  }
  const top = h("div", "sr-facts", `
    <span class="stat-pair"><span class="v">${t.rate == null ? "—" : fmtPct(t.rate)}</span>
      <span class="k">3-bet of ${t.faced_opens} raises${t.target ? ` · ref ~${fmtPct(t.target)}` : ""}</span></span>
    <span class="stat-pair"><span class="v">${t.bluffs}/${t.three_bets.length}</span>
      <span class="k">of them bluffs</span></span>`);
  $(".stat-pair:last-child .k", top).appendChild(info(termTip("3-bet bluff")));
  panel.appendChild(top);
  const line = (label, list, term) => {
    if (!list.length) return;
    const row = h("div", "sr-spot-line", `<span class="small muted">${label}</span>`);
    if (term) $("span", row).appendChild(info(termTip(term)));
    row.appendChild(refChips(list, data.hero_id));
    panel.appendChild(row);
  };
  line("3-bet", t.three_bets);
  line("flatted, could 3-bet", t.flatted_bluffs, "3-bet bluff");
  const vs = t.vs;
  if (vs) {
    const n = vs.n;
    panel.appendChild(h("h3", "", "when you were 3-bet"));
    panel.appendChild(h("div", "sr-facts", `
      <span class="stat-pair"><span class="v">${vs.folded}/${n}</span>
        <span class="k">folded${vs.fold_target ? ` · ref ~${fmtPct(vs.fold_target)}` : ""}</span></span>
      <span class="stat-pair"><span class="v">${vs.called}/${n}</span><span class="k">called</span></span>
      <span class="stat-pair"><span class="v">${vs.four_bet}/${n}</span>
        <span class="k">4-bet${vs.four_bet_target ? ` · ref ~${fmtPct(vs.four_bet_target)}` : ""}</span></span>`));
    line("weak calls", vs.weak_calls, "weak call vs 3-bet");
    line("every answer", vs.decisions);
  }
  return panel;
}

/* ---- folds by bet size ---- */
function foldSizePanel(data) {
  const panel = h("div", "panel", `<h2 id="sr-fs-head">your folds, by bet size</h2>
    <div class="panel-lead">Heads-up, your first answer to a bet on each street.
      Tick: the most a bet that size lets you fold.</div>`);
  $("#sr-fs-head", panel).appendChild(info(termTip("most you can fold")));
  const bands = data.folds_by_size || [];
  if (!bands.length) {
    panel.appendChild(h("div", "small muted", "You never faced a heads-up bet after the flop."));
    return panel;
  }
  for (const b of bands) {
    const row = h("div", "sr-spot" + (b.flagged ? " flagged" : ""), `
      <div class="sr-spot-top"><span class="sr-spot-label">${esc(b.label)}</span>
        <span class="small muted sr-spot-n">folded ${b.folded} of ${b.faced} · most ~${fmtPct(b.max_fold)}</span></div>
      <div class="sr-spot-bar"></div>`);
    $(".sr-spot-bar", row).appendChild(vsBar(b.fold_rate, b.max_fold, b.flagged));
    if (b.flagged) row.appendChild(refChips(b.folds, data.hero_id));
    panel.appendChild(row);
  }
  return panel;
}

/* ---- the same numbers across recent sittings ---- */
function trendPanel(data) {
  const tr = data.trend;
  const panel = h("div", "panel", `<h2>across your recent sittings</h2>
    <div class="panel-lead">The measurements above in your last few sittings,
      oldest first. Marked cells are off the reference.</div>`);
  if (!tr || !tr.rows.length) {
    panel.appendChild(h("div", "small muted", "Not enough of your sittings yet to compare."));
    return panel;
  }
  const table = h("div", "scroller");
  const heads = tr.sessions.map(s =>
    `<th class="num">${esc(whenLabel(s.started_at, false))}</th>`).join("");
  table.innerHTML = `<table class="sr-trend"><thead><tr><th></th>${heads}
    <th>ref</th><th></th></tr></thead><tbody></tbody></table>`;
  const tbody = $("tbody", table);
  for (const r of tr.rows) {
    const tr_ = document.createElement("tr");
    const cells = r.cells.map(c => c == null ? `<td class="num muted">—</td>`
      : `<td class="num${c.off ? " off" : ""}" title="${c.n} hands">${fmtPct(c.value)}</td>`).join("");
    tr_.innerHTML = `<td>${esc(r.label)}</td>${cells}<td class="num muted">${fmtPct(r.target)}</td>
      <td><span class="tag sr-status ${esc(r.status.split(" ")[0])}">${esc(r.status)}</span></td>`;
    const tag = $(".sr-status", tr_);
    if (["persistent", "new", "fixed"].includes(r.status)) tag.appendChild(info(termTip(r.status)));
    tbody.appendChild(tr_);
  }
  panel.appendChild(table);
  return panel;
}

/* ---- the table ---- */
function tablePanel(data) {
  const wrap = h("div", "wide villain-view", `<div class="villain-view-head">
    <span class="label-t">The table</span>
    <span class="small muted">Ranked by decisions tonight, then how to beat each
      opponent.</span></div>`);
  const rank = h("div", "panel");
  const players = data.players || [];
  const scores = players.map(p => p.skill_score);
  const close = scores.length > 1 && Math.max(...scores) - Math.min(...scores) < 5;
  rank.innerHTML = `<h2 id="sr-rank-head">ranked by decisions</h2>
    <div class="panel-lead">${close
      ? "Too close to separate on this many hands: treat it as a tie."
      : "From this sitting's hands alone."} Your cards are visible on every
      hand and theirs only at showdown, so your mistakes are easier to find
      than theirs.</div>`;
  $("#sr-rank-head", rank).appendChild(info(termTip("provisional")));
  players.forEach((p, i) => {
    const row = h("div", "sess-row" + (p.is_hero ? " hero-scope hero-sitting" : ""), `
      <div class="sess-head">
        <div class="sess-id">
          <div class="sess-who"><span class="sr-rank">${i + 1}</span>
            <button class="linkbtn sess-name">${esc(p.name)}</button>${
            p.is_hero ? '<span class="tag hero-tag">you</span>' : ""}
            <span class="tag arch ${p.confidence >= 0.5 ? "on" : ""}">${esc(p.archetype)}</span></div>
          <div class="small muted">${p.hands} hands · ${esc(p.regime_label || "")}</div>
        </div>
        <div class="sess-stats">
          <div class="stat-pair"><span class="v ${p.net_bb >= 0 ? "up" : "down"}">${sgn(p.net_bb)}</span>
            <span class="k">bb</span></div>
          <div class="stat-pair sess-skill"><span class="v">${Math.round(p.skill_score)}</span>
            <span class="k">${p.skill == null ? `provisional · ${fmtPct(p.skill_confidence)} sure` : "skill"}</span></div>
        </div>
      </div>`);
    $(".sess-name", row).onclick = () => switchTab("players", p.player_id);
    rank.appendChild(row);
  });
  wrap.appendChild(rank);
  for (const o of data.opponents || []) wrap.appendChild(opponentCard(o, data.hero_id));
  return wrap;
}

function opponentCard(o, heroId) {
  const panel = h("div", "panel", `<div class="spread"><h2 style="margin:0"></h2>
    ${o.usual_archetype ? `<span class="small muted">usually <span class="tag arch on">${
      esc(o.usual_archetype)}</span></span>` : ""}</div>`);
  $("h2", panel).textContent = `how to beat ${o.name}`;
  if (o.stale) {
    const s = o.stale;
    const warn = h("div", "review-tip stop sr-stale", `<b>Their usual read is stale.</b>
      <span class="small"></span>`);
    $("span", warn).textContent = ` The advice for a ${s.archetype} depends on
      ${statLabel(s.stat, null)}, and tonight that was ${fmtPct(s.tonight)} against
      ${fmtPct(s.usual)} usually. Play the player at the table, not the read.`;
    $("b", warn).appendChild(info(termTip("stale read")));
    panel.appendChild(warn);
  }
  if (!o.tips.length) {
    panel.appendChild(h("div", "small muted sr-none",
      "Nothing in tonight's hands clears the bar for a specific adjustment."));
  }
  for (const t of o.tips) {
    const row = h("div", "review-tip keep", `<div class="leak-advice"></div>`);
    $(".leak-advice", row).textContent = t.text;
    if (t.hands.length) row.appendChild(refChips(t.hands, heroId));
    panel.appendChild(row);
  }
  if (o.changes.length) {
    const block = h("div", "howblock", `<div class="howlabel">different from their usual game</div>`);
    $(".howlabel", block).appendChild(info(termTip("changed tonight")));
    for (const c of o.changes) {
      const row = h("div", "sr-change small", `<span class="sr-change-stat"></span>
        <span class="mono">${fmtPct(c.tonight)}</span>
        <span class="muted">tonight vs ${fmtPct(c.usual)} usually · ${esc(c.regime_label)}</span>`);
      $(".sr-change-stat", row).textContent = statLabel(c.stat, null);
      bindTip($(".sr-change-stat", row), statTip(c.stat, statLabel(c.stat, null)));
      block.appendChild(row);
    }
    panel.appendChild(block);
  }
  return panel;
}
