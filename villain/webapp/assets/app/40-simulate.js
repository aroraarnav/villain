async function viewPlay() {
  const view = $("#view");
  if (!onScreen("play")) return;
  if (state.analysis) { renderAnalysis(view, state.analysis); return; }
  if (state.game) {
    if (!state.paused) thawSimClock();
    renderTable(view, state.game);
    return;
  }
  view.innerHTML = `<div class="panel"><h2>Simulate</h2>
    <div class="small muted" style="margin:-6px 0 16px">Sit at a table and play real hands
      against players from your database. Each villain acts from their own measured profile —
      loose ones call wide, nits fold, aggressive ones barrel — so it plays like practice
      against the people you actually face. Pick up to five and sit down.</div>
    <div id="pick-list" class="pick-list"></div>
    <div class="sit-controls">
      <label class="small muted">stack <input id="sit-stack" type="number" value="200" min="20"></label>
      <label class="small muted">blinds <input id="sit-sb" type="number" value="1" min="1">
        / <input id="sit-bb" type="number" value="2" min="2"></label>
      <button class="act primary" id="sit-go" disabled>Sit down</button>
    </div></div>`;
  $("#pick-list").appendChild(loadingBlock("Reading your database\u2026"));
  const roster = await get("/api/roster");
  if (!onScreen("play") || state.game || state.analysis) return;
  const players = (roster.players || [])
    .filter(p => p.player_id != null && p.player_id !== roster.hero_id && p.hands >= 30)
    .sort((a, b) => b.hands - a.hands);
  const list = $("#pick-list");
  if (!list) return;
  list.innerHTML = "";
  if (!players.length) { list.innerHTML = `<div class="small muted">No players with
    enough hands yet — import some on the Database tab.</div>`; return; }
  const picked = new Set();
  for (const p of players) {
    const b = h("button", "pick", `<span class="name">${esc(p.name)}</span>
      <span class="small muted">${p.hands} hands · ${esc(p.archetype)} · GTO ${
        p.gto != null ? Math.round(p.gto) : "—"}</span>`);
    b.onclick = () => {
      if (picked.has(p.player_id)) { picked.delete(p.player_id); b.classList.remove("on"); }
      else if (picked.size < 5) { picked.add(p.player_id); b.classList.add("on"); }
      $("#sit-go").disabled = picked.size === 0;
    };
    list.appendChild(b);
  }
  $("#sit-go").onclick = async () => {
    $("#sit-go").disabled = true;
    ensureAudio();
    try {
      const data = await post("/api/sim/new", {villains: [...picked],
        stack: +$("#sit-stack").value, sb: +$("#sit-sb").value, bb: +$("#sit-bb").value});
      resetSimClock();
      state.simGen++;
      state.game = data;
      state.paused = false;          // a hold belongs to the table you left
      state.analysis = null;
      if (onScreen("play")) renderTable($("#view"), data);
    } catch (err) { $("#sit-go").disabled = false; alert(err.message); }
  };
}

const SIM_DELAY = 3000;                 // one beat before every auto-action
const SIM_COMPACT = window.matchMedia("(max-width: 780px)");
SIM_COMPACT.addEventListener("change", () => {
  if (state.game && state.tab === "play" && !state.analysis)
    renderTable($("#view"), state.game);
});
let _actx = null;
function ensureAudio() {
  try {
    _actx = _actx || new (window.AudioContext || window.webkitAudioContext)();
    if (_actx.state === "suspended") _actx.resume();
  } catch (e) {}
}
function chipSound() {
  if (state.muted || !_actx) return;
  try {
    const ctx = _actx, t = ctx.currentTime;
    for (let k = 0; k < 3; k++) {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "triangle"; o.frequency.value = 850 + Math.random() * 550;
      const s0 = t + k * 0.028;
      g.gain.setValueAtTime(0.0001, s0);
      g.gain.exponentialRampToValueAtTime(0.07, s0 + 0.004);
      g.gain.exponentialRampToValueAtTime(0.0001, s0 + 0.06);
      o.connect(g); g.connect(ctx.destination);
      o.start(s0); o.stop(s0 + 0.07);
    }
  } catch (e) {}
}

// A knuckle-rap on the felt: two short low thuds, the way a check sounds at a
// real table. Deliberately duller and quieter than the chips so a table full
// of checks never drowns out the action that costs someone money.
function checkSound() {
  if (state.muted || !_actx) return;
  try {
    const ctx = _actx, t = ctx.currentTime;
    for (let k = 0; k < 2; k++) {
      const o = ctx.createOscillator(), g = ctx.createGain(), f = ctx.createBiquadFilter();
      o.type = "triangle"; o.frequency.value = 220 - k * 40;
      f.type = "lowpass"; f.frequency.value = 1400;
      const s0 = t + k * 0.075;
      g.gain.setValueAtTime(0.0001, s0);
      g.gain.exponentialRampToValueAtTime(0.16, s0 + 0.003);
      g.gain.exponentialRampToValueAtTime(0.0001, s0 + 0.11);
      o.connect(f); f.connect(g); g.connect(ctx.destination);
      o.start(s0); o.stop(s0 + 0.1);
    }
  } catch (e) {}
}

// Two cards sliding away across the felt: a short noise burst under a lowpass
// that closes as it goes, which is what a muck actually sounds like. Softest
// of the three -- folding is the one action nobody needs announced.
let _noise = null;
function noiseBuffer(ctx) {
  if (_noise) return _noise;
  const n = Math.floor(ctx.sampleRate * 0.3);
  _noise = ctx.createBuffer(1, n, ctx.sampleRate);
  const d = _noise.getChannelData(0);
  for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / n);
  return _noise;
}
function foldSound() {
  if (state.muted || !_actx) return;
  try {
    const ctx = _actx, t = ctx.currentTime;
    const src = ctx.createBufferSource(), g = ctx.createGain(), f = ctx.createBiquadFilter();
    src.buffer = noiseBuffer(ctx);
    f.type = "lowpass";
    f.frequency.setValueAtTime(3200, t);
    f.frequency.exponentialRampToValueAtTime(700, t + 0.2);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(0.13, t + 0.015);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.24);
    src.connect(f); f.connect(g); g.connect(ctx.destination);
    src.start(t); src.stop(t + 0.22);
  } catch (e) {}
}

//: One place that maps an action to the noise it makes.
function actionSound(action) {
  if (action === "call" || action === "raise") chipSound();
  else if (action === "check") checkSound();
  else if (action === "fold") foldSound();
}

function formatLogLine(line) {
  const street = line.match(/^(flop|turn|river):/i);
  if (street) return street[1][0].toUpperCase() + street[1].slice(1).toLowerCase();
  return line.replace(/\braises to\b/, "raises").replace(/\braise to\b/, "raise");
}

function logLineKind(line) {
  if (/^(flop|turn|river):/i.test(line)) return "log-street";
  if (/\bposts?\b/.test(line)) return "log-post";
  if (/\bwin[s]?\b/.test(line)) return "log-win";
  return "log-act";
}

function actionText(ev) {
  if (!ev) return "";
  if (ev.action === "fold") return "Folds";
  if (ev.action === "check") return "Checks";
  if (ev.action === "call") return "Calls";
  if (ev.opening) return `Bets ${ev.amount}`;
  return `Raises to ${ev.amount}`;
}

async function simPost(route, extra) {
  if (!state.game) return;
  const token = state.game.token;
  const gen = state.simGen;
  clearSimTimer();
  try {
    const d = await post(route, Object.assign({token}, extra || {}));
    if (gen !== state.simGen || !state.game || state.game.token !== token) return;
    state.game = {token, state: d.state};
    state.stepUntil = null;
    state.clockHold = null;
    if (route === "/api/sim/next") {
      state.lastEvent = null; state.revealed = false;
      state.dealHand = null; state.dealUntil = null;
    }
    if (state.tab === "play" && !state.analysis) renderTable($("#view"), state.game);
    else armSimClock();
  } catch (err) { /* game gone or navigated away */ }
}

function clearSimTimer() {
  if (state.stepTimer) { clearTimeout(state.stepTimer); state.stepTimer = null; }
}

function resetSimClock() {
  clearSimTimer();
  state.dealHand = null; state.dealUntil = null; state.stepUntil = null;
  state.clockHold = null; state.revealed = false;
}

// Leaving the Simulate tab, pausing, or hiding the browser tab used to either
// keep firing into a missing DOM or clear the timer and never put it back.
// Hold the remaining wait; thaw it when the table is visible again.
function holdSimClock() {
  clearSimTimer();
  if (state.clockHold) return;
  if (!state.dealUntil && !state.stepUntil) return;
  state.clockHold = {
    dealUntil: state.dealUntil, stepUntil: state.stepUntil, heldAt: Date.now(),
  };
  state.dealUntil = null;
  state.stepUntil = null;
}

function thawSimClock() {
  if (!state.clockHold) return;
  const dt = Date.now() - state.clockHold.heldAt;
  if (state.clockHold.dealUntil) state.dealUntil = state.clockHold.dealUntil + dt;
  if (state.clockHold.stepUntil) state.stepUntil = state.clockHold.stepUntil + dt;
  state.clockHold = null;
}

function armSimClock() {
  clearSimTimer();
  if (!state.game || state.paused || state.clockHold || state.analysis) return;
  if (state.tab !== "play") return;
  const st = state.game.state;
  // Bots and an armed check/fold share one wait. Profile think times and a
  // same-paint auto-fold made the table skip: a 400ms snap next to a long
  // tank, then your cards in the muck the instant theirs hit the felt.
  if (!st.over && (!st.your_turn || cfArmed(st))) {
    if (state.stepUntil == null) state.stepUntil = Date.now() + SIM_DELAY;
    const left = Math.max(0, state.stepUntil - Date.now());
    const token = state.game.token;
    const gen = state.simGen;
    state.stepTimer = setTimeout(() => {
      state.stepUntil = null;
      if (gen !== state.simGen || !state.game || state.game.token !== token) return;
      if (state.game.state.your_turn) fireCheckFold();
      else stepBots(token);
    }, left);
  } else if (st.over && !state.revealed) {
    if (state.dealHand !== st.hand_no) {
      state.dealHand = st.hand_no;
      state.dealUntil = Date.now() + SIM_DELAY;
    }
    tickDealCountdown();
  } else {
    paintDealCount();
  }
}

function renderTable(view, data) {
  // Paint only while Simulate is on screen. A reply that lands after a tab
  // switch used to either draw the table over Database, or clear the timer
  // and leave the session frozen when you came back.
  if (state.tab !== "play" || state.analysis) return;
  clearSimTimer();
  const st = data.state, n = st.seats.length;
  const pnl = st.pnl || 0;
  const pnlBb = st.bb ? (pnl / st.bb).toFixed(1) : "0";
  view.innerHTML = `<div class="panel sim-panel"><div class="sim-layout">
    <div class="sim-main">
      <div class="spread"><h2 style="margin:0">hand ${st.hand_no} <span
        class="muted" style="font-weight:400">· ${esc(st.street)}</span></h2>
        <button class="linkbtn" id="leave">Leave table</button></div>
      <div class="poker-table" id="ptable">
        <div class="felt-oval"></div>
        <div class="table-center">
          <div class="pot-pill">pot <b>${st.pot_mid ?? st.pot}</b></div>
          <div class="board" id="board"></div>
          <div class="deal-count idle" id="deal-count" aria-hidden="true"></div>
        </div>
      </div>
      <div class="controls" id="controls"></div>
    </div>
    <div class="sim-side">
      <div class="sim-pnl">
        <div class="side-label">session P/L</div>
        <div class="pnl-big ${pnl >= 0 ? "up" : "down"}">${pnl >= 0 ? "+" : ""}${pnl}</div>
        <div class="small muted">${pnl >= 0 ? "+" : ""}${pnlBb} bb · ${st.hand_no} hands</div>
        <div class="small muted">blinds ${st.sb}/${st.bb}</div>
      </div>
      <!-- Three session modes, one control shape. Two of them were checkboxes
           and the third (check/fold) a filled pill, which made "armed" mean two
           different things on one screen. They are all cf-toggles now: filled
           is on, outlined is off, readable from across the table view. -->
      <div class="side-modes">
        <button class="act small cf-toggle${state.descOn ? " on" : ""}"
          id="desc-on" aria-pressed="${state.descOn}">Explain</button>
        <button class="act small cf-toggle${state.muted ? "" : " on"}"
          id="snd-on" aria-pressed="${!state.muted}">Sounds</button>
        <button class="act small cf-toggle${state.paused ? " on" : ""}"
          id="pause-on" aria-pressed="${state.paused}">Pause</button>
      </div>
      <button class="act small" id="end-session">End and analyze</button>
      <div class="handlog">
        <div class="handlog-title">Hand log</div>
        <div class="handlog-body" id="handlog"></div>
      </div>
    </div>
  </div></div>`;
  const table = $("#ptable");
  const compact = SIM_COMPACT.matches;
  const rx = compact ? 33 : 40, ry = compact ? 30 : 36;
  st.seats.forEach((s, i) => {
    const theta = Math.PI / 2 + (i / n) * 2 * Math.PI;   // you (0) at the bottom
    // Sit on the felt rim, inside the stage. 45/44 hung the hero through the
    // bottom of the box and over the action bar. Phone radii sit further in
    // so a 92px plate still clears the controls.
    const x = 50 + rx * Math.cos(theta), y = 50 + ry * Math.sin(theta);
    const seat = document.createElement("div");
    const ev = state.lastEvent;
    const thinking = s.to_act && !st.over && (!s.is_hero || cfArmed(st));
    seat.className = "tseat" + (s.is_hero ? " me hero-scope" : "")
      + (s.folded ? " folded" : "") + (s.to_act ? " acting" : "") + (s.won ? " won" : "");
    seat.style.left = x + "%"; seat.style.top = y + "%";
    const shownHole = (state.revealed && s.all_hole) ? s.all_hole : s.hole;
    const cards = shownHole ? shownHole.map(c => cardHtml(c, s.is_hero)).join("")
      : (s.folded ? "" : '<span class="cardback sm"></span><span class="cardback sm"></span>');
    const acted = ev && ev.seat === i && !s.is_hero;
    seat.innerHTML = `<div class="tseat-cards">${cards}</div>
      <div class="tseat-body">
        <div class="tseat-name">${esc(s.name)}${
          s.is_hero && s.name.toLowerCase() !== "you"
            ? ' <span class="tag hero-tag">you</span>' : ""}${
          thinking ? '<span class="spinner sm" aria-hidden="true"></span>' : ""}</div>
        ${acted ? `<div class="tseat-act">${esc(actionText(ev))}</div>` : ""}
        ${s.is_hero ? `<div class="tseat-made${s.made ? "" : " blank"}">${
          s.made ? esc(s.made) : "—"
        }</div>` : ""}
        <div class="tseat-stack">${s.stack}${
          st.over && s.net ? ` <span class="won-amt${s.net < 0 ? " down" : ""}">${
            s.net > 0 ? "+" : ""}${s.net}</span>` : ""}</div>
      </div>`;
    if (acted) {
      const why = (ev.reason || "").split("—").slice(1).join("—").trim();
      const bubble = document.createElement("div");
      // Outward off the felt, and anchored to whichever edge keeps it inside
      // the table. Centered on a seat, a bubble on a right-hand seat ran off
      // the table and covered the sidebar's End button while a villain thought.
      bubble.className = "think-bubble" + (y > 50 ? " below" : "")
        + (x > 66 ? " from-right" : x < 34 ? " from-left" : "");
      bubble.innerHTML = `<b>${esc(actionText(ev))}</b>${
        state.descOn && why ? `<div class="why">${esc(why)}</div>` : ""}`;
      seat.appendChild(bubble);
    }
    table.appendChild(seat);
    if (s.is_button) {
      const d = h("div", "dealer-btn"); d.textContent = "D";
      d.style.left = (50 + (compact ? 24 : 29) * Math.cos(theta - 0.4)) + "%";
      d.style.top = (50 + (compact ? 20 : 24) * Math.sin(theta - 0.4)) + "%";
      table.appendChild(d);
    }
    if (s.committed > 0 && !st.over) {
      const chip = h("div", "tbet");
      chip.style.left = (50 + (compact ? 19 : 23) * Math.cos(theta)) + "%";
      chip.style.top = (50 + (compact ? 16 : 19) * Math.sin(theta)) + "%";
      chip.innerHTML = `<span class="chip-dot"></span>${s.committed}`;
      table.appendChild(chip);
    }
  });
  const boardCards = st.board || [];
  $("#board").innerHTML = [0, 1, 2, 3, 4].map(i =>
    boardCards[i] ? cardHtml(boardCards[i], true)
      : '<span class="card big ghost" aria-hidden="true"></span>').join("");
  const logEl = $("#handlog");
  logEl.innerHTML = (st.log || []).map(l =>
    `<div class="log-line ${logLineKind(l)}">${esc(formatLogLine(l))}</div>`).join("");
  logEl.scrollTop = logEl.scrollHeight;
  $("#leave").onclick = () => {
    state.simGen++;
    resetSimClock();
    state.game = null; state.lastEvent = null; state.analysis = null;
    viewPlay();
  };
  const armToggle = (el, on) => {
    el.classList.toggle("on", on);
    el.setAttribute("aria-pressed", String(on));
  };
  $("#desc-on").onclick = (e) => {
    state.descOn = !state.descOn;
    armToggle(e.currentTarget, state.descOn);
  };
  $("#snd-on").onclick = (e) => {
    state.muted = !state.muted;
    armToggle(e.currentTarget, !state.muted);
    if (!state.muted) ensureAudio();
  };
  // Pause holds the *auto-advance*, not the game: the villains stop stepping
  // and the next hand stops dealing itself, but your own controls stay live,
  // so a spot you want to sit with does not disappear at the pace of a timer.
  $("#pause-on").onclick = (e) => {
    state.paused = !state.paused;
    armToggle(e.currentTarget, state.paused);
    if (state.paused) holdSimClock();
    else {
      thawSimClock();
      armSimClock();
    }
    paintDealCount();
  };
  $("#end-session").onclick = () => endSession();
  renderControls($("#controls"), data);
  paintDealCount();
  if (state.paused) return;
  armSimClock();
}

function dealSecsLeft() {
  if (state.clockHold && state.clockHold.dealUntil) {
    return Math.max(0, Math.ceil((state.clockHold.dealUntil - state.clockHold.heldAt) / 1000));
  }
  if (!state.dealUntil) return 0;
  return Math.max(0, Math.ceil((state.dealUntil - Date.now()) / 1000));
}

function paintDealCount() {
  const el = $("#deal-count");
  if (!el) return;
  const over = state.game && state.game.state && state.game.state.over;
  const counting = over && !state.revealed
    && !!(state.dealUntil || (state.clockHold && state.clockHold.dealUntil));
  el.classList.toggle("idle", !counting);
  el.setAttribute("aria-hidden", String(!counting));
  if (!counting) return;
  el.innerHTML = `Next hand<b>${Math.max(dealSecsLeft(), 1)}</b>`;
}

function tickDealCountdown() {
  paintDealCount();
  if (state.paused || state.clockHold || state.tab !== "play") return;
  const left = (state.dealUntil || 0) - Date.now();
  if (left <= 0) {
    state.dealUntil = null;
    state.dealHand = null;
    simPost("/api/sim/next");
    return;
  }
  state.stepTimer = setTimeout(tickDealCountdown, Math.min(200, left));
}

async function stepBots(token) {
  const gen = state.simGen;
  try {
    const r = await post("/api/sim/step", {token});
    if (gen !== state.simGen || !state.game || state.game.token !== token) return;
    if (r.event) actionSound(r.event.action);
    state.game = {token, state: r.state};
    state.lastEvent = r.event;
    state.stepUntil = null;
    if (state.clockHold) state.clockHold.stepUntil = null;
    if (state.tab === "play" && !state.analysis) renderTable($("#view"), state.game);
    else armSimClock();
  } catch (err) { /* game gone or navigated away */ }
}

function cfArmed(st) {
  // Armed for *this hand*, not for the session. It has to survive the whole
  // hand -- you arm it on the flop and it should still fold the river -- but
  // a standing instruction that outlives the hand is how you fold aces two
  // hands later without touching anything.
  return state.checkFold && state.checkFoldHand === st.hand_no;
}

function fireCheckFold() {
  const st = state.game && state.game.state;
  if (!st || st.over || !st.your_turn || !st.legal || !cfArmed(st)) return;
  const kind = st.legal.can_check ? "check" : "fold";
  actionSound(kind);
  simPost("/api/sim/act", {kind, amount: 0});
}

function cfToggle(st) {
  // Shown in every state of the table, including while the villains act,
  // which is exactly when you want to set it.
  const armed = cfArmed(st);
  const b = h("button", "act cf-toggle" + (armed ? " on" : ""));
  b.textContent = "Check / Fold";
  b.setAttribute("aria-pressed", armed ? "true" : "false");
  b.title = armed
    ? "Armed for this hand: checks when it can, folds when it cannot. Click to disarm."
    : "Check when possible, fold when facing a bet — for the rest of this hand.";
  b.onclick = () => {
    const on = !cfArmed(st);
    state.checkFold = on;
    state.checkFoldHand = on ? st.hand_no : null;
    if (st.your_turn) {
      // Arming starts a fresh beat; disarming cancels the pending auto-act.
      // Leave the clock alone while villains are thinking — toggling this
      // mid-orbit should not reset their wait.
      state.stepUntil = null;
      if (state.clockHold) state.clockHold.stepUntil = null;
    }
    renderTable($("#view"), state.game);
  };
  return b;
}

function renderControls(el, data) {
  const st = data.state; el.innerHTML = "";
  if (st.over) {
    if (!state.revealed && st.seats.some(x => x.all_hole && !x.is_hero)) {
      el.appendChild(actbtn("Reveal cards", () => {
        clearSimTimer();
        state.revealed = true;
        state.dealUntil = null; state.dealHand = null;
        if (state.clockHold) state.clockHold.dealUntil = null;
        renderTable($("#view"), data);
      }));
    }
    el.appendChild(actbtn("Deal now", () => simPost("/api/sim/next"), "primary"));
    el.appendChild(cfToggle(st));
    const won = st.seats.filter(s => s.won);
    if (won.length) {
      const note = h("span", "small muted");
      note.textContent = won.map(s => `${s.name} +${s.won}`).join(" · ");
      el.appendChild(note);
    }
    return;
  }
  if (!st.your_turn) {
    const note = h("span", "small muted"); note.textContent = "villains acting…";
    el.appendChild(note);
    el.appendChild(cfToggle(st));
    return;
  }
  const lg = st.legal;
  const act = (kind, amount) => {
    actionSound(kind);
    simPost("/api/sim/act", {kind, amount: amount || 0});
  };
  if (cfArmed(st)) {
    // Wait the same beat as a villain. Acting on this paint was a snap
    // fold — their bet and your muck landed in the same frame.
    const note = h("span", "small muted");
    note.textContent = lg.can_check ? "checking…" : "folding…";
    el.appendChild(note);
    el.appendChild(cfToggle(st));
    return;
  }
  el.appendChild(cfToggle(st));
  const leave = h("div", "controls-leave");
  if (lg.can_fold) leave.appendChild(actbtn("Fold", () => act("fold")));
  if (lg.can_check) leave.appendChild(actbtn("Check", () => act("check")));
  if (leave.childNodes.length) el.appendChild(leave);
  if (lg.can_raise) {
    const facing = lg.can_call && !lg.can_check;
    const callTo = lg.committed + lg.call_amount;
    const potAfter = st.pot + lg.call_amount;
    const sizeTo = (frac) => {
      const raw = lg.can_check ? Math.round(frac * st.pot)
        : callTo + Math.round(frac * potAfter);
      return Math.min(lg.max_raise_to, Math.max(lg.min_raise_to, raw));
    };
    const snap = (v) => {
      v = +v;
      if (facing && v > callTo && v < lg.min_raise_to) {
        const mid = (callTo + lg.min_raise_to) / 2;
        return v < mid ? callTo : lg.min_raise_to;
      }
      return v;
    };
    const labelFor = (v) => {
      v = snap(v);
      if (facing && v <= callTo) return `Call ${lg.call_amount}`;
      return lg.can_check ? `Bet ${v}` : `Raise to ${v}`;
    };
    const wrap = h("div", "raise-wrap");
    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = facing ? callTo : lg.min_raise_to;
    slider.max = lg.max_raise_to;
    slider.value = facing ? callTo : sizeTo(0.66);
    const amt = h("span", "raise-amt");
    const go = actbtn("", () => {
      const v = snap(+slider.value);
      if (facing && v <= callTo) act("call");
      else act("raise", v);
    }, "primary commit");
    const upd = () => {
      const v = snap(+slider.value);
      slider.value = v;
      amt.textContent = facing && v <= callTo ? "call" : `to ${v}`;
      go.textContent = labelFor(v);
    };
    slider.oninput = upd; upd();
    const presets = h("div", "presets");
    if (facing) {
      presets.appendChild(actbtn("call", () => { slider.value = callTo; upd(); }, "small"));
    }
    for (const [label, frac] of [["⅓", 1 / 3], ["½", 0.5], ["⅔", 2 / 3], ["pot", 1.0]]) {
      presets.appendChild(actbtn(label, () => { slider.value = sizeTo(frac); upd(); }, "small"));
    }
    presets.appendChild(actbtn("all-in", () => { slider.value = lg.max_raise_to; upd(); }, "small"));
    wrap.append(presets, slider, amt);
    el.appendChild(wrap);
    el.appendChild(go);
  } else if (lg.can_call) {
    el.appendChild(actbtn(`Call ${lg.call_amount}`, () => act("call"), "primary commit"));
  }
}

async function endSession() {
  if (!state.game) return;
  const token = state.game.token;
  state.simGen++;
  const gen = state.simGen;
  clearSimTimer();
  try {
    const r = await post("/api/sim/analysis", {token});
    if (gen !== state.simGen) return;
    state.analysis = r.analysis;
    state.game = null;
    state.lastEvent = null;
    resetSimClock();
    if (onScreen("play")) renderAnalysis($("#view"), state.analysis);
  } catch (err) {
    if (state.game && onScreen("play")) armSimClock();
    alert(err.message);
  }
}

function renderAnalysis(view, a) {
  const money = (c, bb) => `${c >= 0 ? "+" : ""}${c} <span class="muted">(${
    c >= 0 ? "+" : ""}${bb} bb)</span>`;
  const stat = (label, v) => `<div class="astat"><div class="astat-v">${
    v == null ? "—" : v + "%"}</div><div class="astat-l">${label}</div></div>`;
  view.innerHTML = `<div class="panel"><div class="spread"><h2 style="margin:0">session analysis</h2>
      <button class="linkbtn" id="a-back">back to setup</button></div>
    <div class="small muted" style="margin:-4px 0 16px">${a.hands} hands played.</div>
    <div class="a-headline">
      <div class="pnl-big ${a.pnl >= 0 ? "up" : "down"}">${a.pnl >= 0 ? "+" : ""}${a.pnl}</div>
      <div class="small muted">${a.pnl_bb >= 0 ? "+" : ""}${a.pnl_bb} bb · ${
        a.bb100 >= 0 ? "+" : ""}${a.bb100} bb/100 over ${a.hands} hands</div>
    </div>
    ${(a.lessons && a.lessons.length) ? `<div class="lessons">${
      a.lessons.map(l => `<div class="lesson">${esc(l)}</div>`).join("")
    }</div>` : ""}
    <h3 style="margin:20px 0 8px">your line this session</h3>
    <div class="astats">${stat("VPIP", a.vpip)}${stat("PFR", a.pfr)}${
      stat("aggression", a.aggression)}${stat("to showdown", a.went_to_showdown)}${
      stat("won at SD", a.won_at_showdown)}</div>
    <h3 style="margin:22px 0 8px">against each villain</h3>
    <div class="a-vs"></div>
    <h3 style="margin:22px 0 8px">swings</h3>
    <div class="small">Biggest pot won: ${a.best ? `hand ${a.best.hand_no}, ${money(a.best.net, (a.best.net/1).toFixed(0))}`.replace(/\(.*\)/, "") : "—"}</div>
    <div class="small">Worst hand: ${a.worst ? `hand ${a.worst.hand_no}, ${a.worst.net}` : "—"}</div>
  </div>`;
  const vs = $(".a-vs", view);
  for (const v of a.vs) {
    const row = h("div", "a-vs-row");
    row.innerHTML = `<span>${esc(v.name)}</span>
      <span class="${v.net >= 0 ? "up" : "down"}" style="font-variant-numeric:tabular-nums">${
        v.net >= 0 ? "+" : ""}${v.net} <span class="muted">(${
        v.net_bb >= 0 ? "+" : ""}${v.net_bb} bb)</span></span>`;
    vs.appendChild(row);
  }
  $("#a-back").onclick = () => {
    state.analysis = null; state.game = null; state.lastEvent = null;
    viewPlay();
  };
}
