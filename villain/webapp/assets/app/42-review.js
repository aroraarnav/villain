/* ---- simulate: the session review ----
   Everything on this screen is the session that just ended. The simulator
   dealt every card, so a fold, a call or a bluff can be put next to the hand
   the villain really held -- the one thing no hand history can show, and the
   reason this review exists. Tips cite the hands they came from, and every
   hand number opens that hand face up. */

const signed = v => `${v > 0 ? "+" : ""}${v}`;

function renderAnalysis(view, a) {
  const dash = h("div", "dash sim-review");
  dash.appendChild(reviewHead(a));
  dash.appendChild(keepStop(a));
  dash.appendChild(reviewLine(a));
  dash.appendChild(reviewRange(a));
  dash.appendChild(reviewDecisions(a));
  const vhead = h("div", "wide villain-view", `<div class="villain-view-head">
    <span class="label-t">The villains</span>
    <span class="small muted">How each one played today, with their cards face
      up.</span></div>`);
  for (const v of a.villains) vhead.appendChild(villainReview(v));
  dash.appendChild(vhead);
  dash.appendChild(keyHands(a));
  view.innerHTML = "";
  view.appendChild(dash);
}

function reviewHead(a) {
  const g = a.graded;
  const flagged = g.decisions - g.right;
  const head = h("div", "panel wide review-head", `
    <div class="spread"><h2 style="margin:0">session review</h2>
      <div class="row">
        ${state.simSetup ? `<button class="act small primary" id="a-again">Sit down again</button>` : ""}
        <button class="act small" id="a-back">New table</button>
      </div></div>
    <div class="review-top">
      <div class="review-pnl">
        <div class="pnl-big ${a.pnl >= 0 ? "up" : "down"}">${signed(a.pnl)}</div>
        <div class="small muted">${signed(a.pnl_bb)} bb · ${signed(a.bb100)} bb/100
          over ${a.hands} hand${a.hands === 1 ? "" : "s"}</div>
      </div>
      <div class="graded-head">
        <div class="stat-pair"><span class="v">${g.decisions}</span>
          <span class="k">graded</span></div>
        <div class="stat-pair"><span class="v">${g.right}</span>
          <span class="k">right</span></div>
        <div class="stat-pair"><span class="v${flagged ? " warnv" : ""}">${flagged}</span>
          <span class="k">flagged</span></div>
        <div class="graded-rate"><div class="graded-bar"></div>
          <div class="small muted">${g.decisions
            ? `${fmtPct(g.right / g.decisions)} right against the cards they held`
            : "nothing to grade yet"}</div></div>
      </div>
    </div>
    <p class="panel-lead review-verdict"></p>`);
  if (g.decisions) {
    const b = bar(g.right, g.decisions, "var(--mark-1)", 999);
    b.setAttribute("preserveAspectRatio", "none");
    $(".graded-bar", head).appendChild(b);
  }
  $(".graded-head .stat-pair .k", head).appendChild(info(termTip("graded decision")));
  // The chip count over a short session is mostly the cards. Say so once, at
  // the top, so the number in green or red is not read as the verdict.
  $(".review-verdict", head).textContent = !g.decisions
    ? "No postflop spot this session could be graded against their cards. "
      + "Play more pots past the flop and this fills in."
    : a.hands < 200
      ? `${a.hands} hands is too few for the result to say much -- it is mostly `
        + "the cards. The graded decisions below are the part you control."
      : "The result still swings with the cards at this length. The graded "
        + "decisions below are the part you control.";
  $("#a-back", head).onclick = () => {
    state.analysis = null; state.game = null; state.lastEvent = null;
    viewPlay();
  };
  const again = $("#a-again", head);
  if (again) again.onclick = async () => {
    again.disabled = true;
    try { await sitDown(state.simSetup); }
    catch (err) { again.disabled = false; alert(err.message); }
  };
  return head;
}

/* A hand number, as a control. The tips, the decision rows and the villain
   cards all cite hands; this is the one way any of them opens one. */
function handChips(nums) {
  const wrap = h("span", "hand-chips");
  for (const n of nums || []) {
    const b = h("button", "hand-chip");
    b.textContent = `#${n}`;
    b.title = `open hand ${n}`;
    b.onclick = () => openSimHand(n);
    wrap.appendChild(b);
  }
  return wrap;
}

function tipRow(t, cls) {
  const row = h("div", `review-tip ${cls}`, `<div class="leak-advice"></div>`);
  $(".leak-advice", row).textContent = t.text;
  if (t.hands && t.hands.length) row.appendChild(handChips(t.hands));
  return row;
}

function keepStop(a) {
  const cols = h("div", "dash-cols wide");
  for (const [key, title, cls, empty] of [
    ["keep", "keep doing", "keep", "Nothing this session clears the bar for praise yet."],
    ["stop", "stop doing", "stop", "Nothing in this session's hands needs fixing."],
  ]) {
    const col = h("div", "col");
    const panel = h("div", "panel", `<h2>${title}</h2>`);
    const list = a[key] || [];
    if (!list.length) panel.appendChild(h("div", "empty small muted", esc(empty)));
    for (const t of list) panel.appendChild(tipRow(t, cls));
    col.appendChild(panel);
    cols.appendChild(col);
  }
  return cols;
}

function reviewLine(a) {
  const panel = h("div", "panel wide", `<h2>your line this session</h2>
    <div class="astats"></div>`);
  const row = $(".astats", panel);
  for (const s of a.line) {
    const cell = h("div", "astat", `
      <div class="astat-v">${s.value == null ? "—" : fmtPct(s.value)}</div>
      <div class="astat-l">${esc(s.label)}</div>
      <div class="small muted">${s.hits} of ${s.chances}</div>`);
    _heroVoice = true;
    const tip = statTip(s.stat, s.label);
    _heroVoice = false;
    $(".astat-l", cell).appendChild(info(tip));
    row.appendChild(cell);
  }
  return panel;
}

function reviewRange(a) {
  const cols = h("div", "dash-cols wide");
  const grid = h("div", "col", `<div class="panel">
    <h2>your preflop range this session</h2>
    <div class="panel-lead">Every hand you were dealt. Darker means you played it
      (raised or called) more often.</div>
    <div class="grid-slot"></div>
    <div class="range-legend"><span>never</span><span class="ramp"></span><span>always</span></div>
  </div>`);
  $(".grid-slot", grid).appendChild(rangeGrid(a.grid));
  const pos = h("div", "col", `<div class="panel"><h2>by position</h2>
    <div class="pos-slot"></div></div>`);
  const slot = $(".pos-slot", pos);
  const ring = positionRing(a.positions || []);
  if (ring) {
    slot.appendChild(ring);
    slot.insertAdjacentHTML("beforeend", `<div class="panel-lead pos-note">Share
      of hands played from each seat, shaded against your widest.</div>`);
  } else {
    slot.innerHTML = `<div class="small muted">Heads-up has one seat each way;
      the grid is the whole picture.</div>`;
  }
  cols.append(grid, pos);
  return cols;
}

const DECISION_ROWS = [
  ["fold", "folds facing a bet", "folded with the price"],
  ["call", "calls facing a bet", "paid off"],
  ["bluff", "no-pair bets", "no-pair bet"],
  ["check", "river checks with the winner", "checked the winner"],
];

function reviewDecisions(a) {
  const panel = h("div", "panel wide", `<h2 class="dec-head">decisions, against their cards</h2>
    <div class="panel-lead">Each postflop fold and call priced against the hand
      the bettor really held; each bet with no pair judged by whether it took
      the pot. Heads-up spots only.</div>`);
  $(".dec-head", panel).appendChild(info(termTip("graded decision")));
  for (const [kind, label, term] of DECISION_ROWS) {
    const d = a.decisions[kind];
    if (!d || !d.n) continue;
    // A river check with the winner is only ever a miss, so it is a count,
    // not a rate.
    const miss = kind === "check";
    const row = h("div", "dec-row", `
      <span class="dec-label">${esc(label)}</span>
      <span class="dec-bar"></span>
      <span class="small muted dec-val">${miss
        ? `${d.n} missed` : `${d.right} of ${d.n} right`}</span>
      <span class="dec-wrong"></span>`);
    $(".dec-label", row).appendChild(info(termTip(term)));
    if (!miss) {
      const b = bar(d.right, d.n, d.right === d.n ? "var(--mark-1)" : "var(--warn)", 150);
      b.setAttribute("preserveAspectRatio", "none");
      $(".dec-bar", row).appendChild(b);
    }
    $(".dec-wrong", row).appendChild(handChips(d.wrong));
    panel.appendChild(row);
  }
  if (!panel.querySelector(".dec-row")) {
    panel.appendChild(h("div", "small muted",
      "No heads-up postflop spots this session. Play more pots past the flop."));
  }
  return panel;
}

function villainReview(v) {
  const panel = h("div", "panel villain-review", `
    <div class="spread">
      <div><span class="name">${esc(v.name)}</span>
        <span class="small muted">· ${v.together} pot${v.together === 1 ? "" : "s"} with you</span></div>
      <span class="${v.net >= 0 ? "up" : "down"} mono">${signed(v.net)}
        <span class="muted">(${signed(v.net_bb)} bb)</span></span>
    </div>
    <div class="vfacts"></div>
    <div class="dash-cols">
      <div class="col v-tips"></div>
      <div class="col v-range">
        <h3>their range, face up</h3>
        <div class="grid-slot"></div>
        <div class="small muted">Every hand they were dealt this session. Darker
          means they played it more often.</div>
      </div>
    </div>`);
  const facts = $(".vfacts", panel);
  const fact = (value, label, tip) => {
    const f = h("div", "stat-pair", `<span class="v">${value}</span>
      <span class="k">${esc(label)}</span>`);
    if (tip) $(".k", f).appendChild(info(tip));
    facts.appendChild(f);
  };
  const rate = (k, n) => n ? `${k}/${n}` : "—";
  fact(rate(v.vpip.hits, v.vpip.chances), "played", statTip("vpip", "VPIP"));
  fact(rate(v.pfr.hits, v.pfr.chances), "raised pre", statTip("pfr", "PFR"));
  fact(rate(v.your_bets.folded, v.your_bets.n), "folded to your bets",
    `<span class="hl">folded to your bets</span><br>Times you bet or raised
     after the flop with them still in the hand, and how many of those they
     folded.`);
  fact(rate(v.their_bets.no_pair, v.their_bets.n), "their bets with no pair",
    `<span class="hl">their bets with no pair</span><br>Times they bet or raised
     into you after the flop, and how many of those they held no pair or
     better on that board -- known now, because their cards are face up.`);

  const tips = $(".v-tips", panel);
  if (!v.do.length && !v.dont.length) {
    tips.appendChild(h("div", "small muted",
      `Not enough hands against them to say anything yet — ${v.together} pot${
        v.together === 1 ? "" : "s"} together.`));
  }
  for (const [label, list, cls] of [["Do", v.do, "keep"], ["Don't", v.dont, "stop"]]) {
    if (!list.length) continue;
    const block = h("div", "howblock", `<div class="howlabel">${label}</div>`);
    for (const t of list) block.appendChild(tipRow(t, cls));
    tips.appendChild(block);
  }
  if (v.showdowns.length) {
    const block = h("div", "howblock", `<div class="howlabel">showdowns with you</div>`);
    for (const s of v.showdowns) {
      const row = h("div", "sd-row", `<button class="hand-chip">#${s.hand_no}</button>
        <span class="sd-cards"></span>
        <span class="small">${esc(s.made)}</span>
        <span class="small muted">${s.won ? "they won" : "they lost"}</span>`);
      $(".hand-chip", row).onclick = () => openSimHand(s.hand_no);
      $(".sd-cards", row).appendChild(cardsEl(s.hole, {small: true}));
      block.appendChild(row);
    }
    tips.appendChild(block);
  }
  $(".grid-slot", panel).appendChild(rangeGrid(v.grid));
  return panel;
}

function keyHands(a) {
  const panel = h("div", "panel wide", `<h2>hands worth another look</h2>
    <div class="panel-lead">Every flagged hand, then the biggest swings. Click one
      to replay it with every card face up.</div>`);
  if (!a.key_hands.length) {
    panel.appendChild(h("div", "small muted", "No hand this session moved enough to flag."));
  }
  for (const k of a.key_hands) {
    const row = h("div", "key-row", `
      <span class="mono small">#${k.hand_no}</span>
      <span class="small muted">${esc(k.position)}</span>
      <span class="key-hole"></span>
      <span class="key-board"></span>
      <span class="key-flags"></span>
      <span class="mono ${k.net >= 0 ? "up" : "down"}">${signed(k.net_bb)} bb</span>`);
    $(".key-hole", row).appendChild(cardsEl(k.hole, {small: true}));
    if (k.board.length) $(".key-board", row).appendChild(cardsEl(k.board, {small: true}));
    else $(".key-board", row).innerHTML = `<span class="small muted">no flop</span>`;
    for (const f of k.flags) {
      const tag = h("span", "tag flag-tag");
      tag.textContent = f;
      $(".key-flags", row).appendChild(tag);
    }
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.onclick = () => openSimHand(k.hand_no);
    row.onkeydown = e => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openSimHand(k.hand_no); }
    };
    panel.appendChild(row);
  }
  return panel;
}

const GRADE_WORDS = {
  fold: d => `${d.street} fold: ${fmtPct(d.equity)} to win against ${d.against}'s ${
    d.their_hole.join(" ")}${d.their_made ? ` (${d.their_made})` : ""}, needed ${fmtPct(d.needed)}`,
  call: d => `${d.street} call: ${fmtPct(d.equity)} to win against ${d.against}'s ${
    d.their_hole.join(" ")}${d.their_made ? ` (${d.their_made})` : ""}, needed ${fmtPct(d.needed)}`,
  bluff: d => `${d.street} bet with no pair: ${d.right ? "took the pot" : "did not get through"}`,
  check: d => `${d.street} check with the winning hand, checked through`,
};

async function openSimHand(n) {
  const modal = sheet(`Hand ${n}`, {host: "#modal2", close: "Back", body: `<div id="sim-hand"></div>`});
  const box = $("#sim-hand", modal);
  box.appendChild(loadingBlock("Opening the hand…"));
  let d;
  try {
    d = await post("/api/sim/hand", {token: state.analysisToken, hand_no: n});
  } catch (err) {
    box.innerHTML = `<p class="err">${esc(/game not found/.test(err.message)
      ? "This session is no longer in memory -- only the last few tables are kept."
      : err.message)}</p>`;
    return;
  }
  box.innerHTML = `<div class="small muted">${esc(d.position)} · ${
    d.made ? esc(d.made) + " · " : ""}<span class="${d.net >= 0 ? "up" : "down"}">${
    signed(d.net_bb)} bb</span></div>
    <div class="hand-board"></div>
    <div class="hand-seats"></div>
    <div class="hand-grades"></div>
    <h3>action</h3><div class="handlog-body hand-log"></div>`;
  if (d.board.length) $(".hand-board", box).appendChild(cardsEl(d.board));
  const seats = $(".hand-seats", box);
  for (const s of d.seats) {
    const row = h("div", "alias-row" + (s.is_hero ? " hero-scope" : ""), `
      <span><b>${esc(s.name)}</b> <span class="small muted">${esc(s.position)}${
        s.folded ? " · folded" : ""}</span></span>
      <span class="seat-cards"></span>
      <span class="small mono ${s.net >= 0 ? "up" : "down"}">${signed(s.net)}</span>`);
    $(".seat-cards", row).appendChild(cardsEl(s.hole, {small: true}));
    seats.appendChild(row);
  }
  const grades = $(".hand-grades", box);
  for (const g of d.graded || []) {
    const line = h("div", `review-tip ${g.right ? "keep" : "stop"}`,
      `<div class="small"></div>`);
    $("div", line).textContent = GRADE_WORDS[g.kind](g);
    grades.appendChild(line);
  }
  $(".hand-log", box).innerHTML = (d.log || []).map(l =>
    `<div class="log-line ${logLineKind(l)}">${esc(formatLogLine(l))}</div>`).join("");
}
