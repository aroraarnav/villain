async function viewHero() {
  const view = $("#view");
  // Blocking, because in the browser it genuinely blocks: no thread to build
  // the hero model on, so nothing responds -- including another tab -- until
  // it is done, and three minutes of that reads as a hung page. The veil turns
  // "nothing responds" into "not yet". Cold builds only; warm, it would be a
  // flash of furniture.
  let cold = false;
  try {
    const peek0 = await get("/api/hero?peek=1");
    // "building" is the same wait as "cold": the work is already running,
    // usually because this tab asked a moment ago and the reader came back.
    // Treating it as warm dropped the veil and left a blank poll.
    cold = peek0.status === "cold" || peek0.status === "building";
  } catch (err) {
    // Do not quietly assume warm. Guessing wrong here means a build that takes
    // minutes runs with no veil and no progress registered -- a bare spinner,
    // which is the one outcome this whole path exists to avoid. Treat an
    // unanswerable peek as cold and say so.
    console.warn("hero: could not check the cache, assuming cold", err);
    cold = true;
  }
  const done = cold
    ? showBusy("Reading your own hands\u2026", undefined)
    : null;
  if (cold) {
    // Say the two things somebody watching a long wait needs to know: that it
    // will finish, and that it will not happen again.
    const note = $("#busy-text");
    if (note) {
      note.insertAdjacentHTML("afterend",
        '<div class="small muted" style="margin-top:6px;max-width:34rem">'
        + 'Fitting a hand-strength model over every hand you have played, so '
        + 'your folds and your sizing can be graded against what you actually '
        + 'held. <b>This runs once</b> — after it, the Hero tab opens instantly '
        + 'until your next import.</div>');
    }
    // Real progress, reported from inside the Python as it walks. Walks over
    // hands are counted per hand; fitting is counted per cross-validation
    // fold. A total of zero means this phase has nothing honest to count.
    const PHASES = {
      starting: "Opening your database",
      finding: "Finding which seat is yours",
      loading: "Reading your hand histories",
      measuring: "Measuring the hands you played",
      reading: "Scoring every hand you played",
      fitting: "Fitting the model to what you held",
      grading: "Grading your folds and your sizing",
    };
    window.__villainProgress = (msg) => {
      const label = PHASES[msg.phase] || "Working";
      const counted = Number(msg.done);
      const total = Number(msg.total);
      if (total > 0) {
        done(`${label}\u2026 ${counted.toLocaleString()} of ${total.toLocaleString()}`,
             counted / total);
      } else {
        done(`${label}\u2026`, undefined);
      }
    };
  }
  let data;
  try {
    data = await get("/api/hero");
    // Local ``villain test`` answers 202 and builds on a thread. Keep the
    // counted veil up and poll peek for the same phases the in-process
    // (browser) build reports directly -- dropping it for "this page will
    // appear on its own" was a loader with no bar.
    while (data && data.status === "building") {
      if (!onScreen("hero")) {
        delete window.__villainProgress;
        return;
      }
      const peek = await get("/api/hero?peek=1");
      if (window.__villainProgress && peek.phase) {
        window.__villainProgress(peek);
      }
      if (peek.status !== "building") {
        data = await get("/api/hero");
        break;
      }
      await new Promise((r) => setTimeout(r, 100));
    }
    delete window.__villainProgress;
    if (done) $("#modal").innerHTML = "";
    if (!onScreen("hero")) return;
    if (state.heroPoll) { clearTimeout(state.heroPoll); state.heroPoll = null; }
  } catch (err) {
    delete window.__villainProgress;
    $("#modal").innerHTML = "";
    view.innerHTML = `<div class="panel"><h2>hero</h2>
      <p class="err">${esc(err.message)}</p></div>`;
    return;
  }
  if (done) $("#modal").innerHTML = "";

  // Hero is a player, so render the full profile card -- header, skill
  // breakdown, your leaks, key numbers: everything a villain's page has, in the
  // same dashboard layout -- then hang the hero-only deep-dives below it.
  // hero:true drops the two opponent-directed pieces (the plan, and the
  // per-leak "do this to them").
  const dash = profileCard(data.self, {heroId: data.hero_id, hero: true});

  // Grid and position breakdown side by side: two views of the same range,
  // one by hand the other by seat. dash-cols is the two-panel layout the
  // skill/read split already uses on a player's own page.
  const rangeCols = h("div", "dash-cols wide");
  const gridCol = h("div", "col", `<div class="panel">
    <h2 id="hero-range-head">preflop range</h2>
    <div class="small muted" style="margin:-6px 0 10px">Cards known on ${fmtPct(data.visibility)} of ${data.hands} hands \u2014 only your own export shows this.</div>
    <div id="hero-grid"></div>
    <div class="range-legend"><span>never</span><span class="ramp"></span><span>always</span></div>
  </div>`);
  const posCol = h("div", "col", `<div class="panel">
    <h2>by position</h2>
    <div id="hero-positions"></div>
  </div>`);
  rangeCols.append(gridCol, posCol);

  const gradesPanel = h("div", "panel wide", `
    <h2 id="hero-grades-head">fold grades &amp; missed value</h2>
    <h3>fold grades</h3>
    <div id="hero-folds"></div>
    <h3>missed value</h3>
    <div id="hero-missed"></div>`);
  dash.appendChild(gradesPanel);
  dash.appendChild(rangeCols);

  const tellsPanel = h("div", "panel wide", `
    <h2 id="hero-tells-head">sizing &amp; timing tells</h2>
    <div class="tellcols">
      <div><h3>sizing</h3><div id="hero-sizing"></div></div>
      <div><h3>timing</h3><div id="hero-timing"></div></div>
    </div>`);
  dash.appendChild(tellsPanel);

  const narrowingPanel = h("div", "panel wide", `
    <h2 id="hero-narrowing-head">range narrowing</h2>
    <div id="hero-narrowing"></div>`);
  dash.appendChild(narrowingPanel);

  // Self machinery first. profileCard builds the same tiles it builds for a
  // villain -- key numbers, priced leaks, skill -- and on this tab those are
  // the *least* interesting thing on the page: they are what any opponent with
  // your hand histories could work out. Fold grades, your real range, and the
  // tells only your own export can see go above them, and the villain view of
  // you is relabelled as what it is and moved to the foot of the page.
  const villainView = h("div", "wide villain-view", `<div class="villain-view-head">
    <span class="label-t">How you look as a villain</span>
    <span class="small muted">The same read the tool would give an opponent
      studying you.</span></div>`);
  for (const sel of [".p-hud", ".p-do", ".p-skill"]) {
    const panel = $(sel, dash);
    if (panel) villainView.appendChild(panel);
  }
  dash.appendChild(villainView);

  $("#modal").innerHTML = "";          // dismiss the loader
  view.innerHTML = "";
  view.appendChild(dash);

  $("#hero-range-head", dash).appendChild(info(
    `Every hand you were ever dealt, not just the ones you played -- something
    only your own export can show. Darker means played (raised or called)
    more often.`));
  $("#hero-grades-head", dash).appendChild(info(
    `${termTip("percentile")}<br><br><span class="hl">fold grades</span> --
    postflop folds, graded against what a bet like that one usually turns out
    to be.<br><br><span class="hl">missed value</span> -- the mirror question,
    asked of checks that could have bet instead.`));
  $("#hero-tells-head", dash).appendChild(info(
    `Does your bet size, or think time, change with the hand behind it?
    Nobody's hand strength is known often enough to ask a villain this --
    yours is known on every bet, not just the ones that reached showdown.`));
  $("#hero-narrowing-head", dash).appendChild(info(
    `A continuing range is supposed to get stronger street by street, as the
    wide ones give up along the way. Average hand strength among hands still
    live, by street, says whether yours does.`));

  $("#hero-grid", dash).appendChild(rangeGrid(data.grid));

  const positions = $("#hero-positions", dash);
  const ranges = [...(data.ranges || [])].sort(
    (a, b) => POSITION_ORDER.indexOf(a.position) - POSITION_ORDER.indexOf(b.position));
  const ring = positionRing(ranges);
  if (ring) {
    positions.appendChild(ring);
    positions.insertAdjacentHTML("beforeend",
      `<div class="panel-lead pos-note">Share of hands played from each seat,
       shaded against your widest.</div>`);
  } else {
    // One seat is not a ring. Fall back to the row the rest of the app uses.
    positions.className = "hero-pos";
    for (const r of ranges) {
      if (!r.hands) continue;
      const played = (r.raised + r.called) / r.hands;
      const row = h("div", "pos-row", `
        <span class="pos-name">${esc(r.position)}<span class="small muted"> ${r.hands}h</span></span>
        <span class="pos-bar"></span>
        <span class="pos-val small muted">${fmtPct(played)} played</span>`);
      const b = bar(played, 1, "var(--mark-3)", 150);
      b.setAttribute("preserveAspectRatio", "none");
      $(".pos-bar", row).appendChild(b);
      positions.appendChild(row);
    }
  }

  if (data.grade_error) {
    $("#hero-folds", dash).innerHTML = `<div class="small muted">${esc(data.grade_error)}</div>`;
    $("#hero-missed", dash).innerHTML = "";
  } else {
    renderGradedSection($("#hero-folds", dash), data.fold_grades, {
      noun: "folds", heroId: data.hero_id,
      verdict: "had more edge than the bet typically shows",
      emptyText: "Not enough postflop folds with a clean line to grade yet.",
    });
    renderGradedSection($("#hero-missed", dash), data.missed_value, {
      noun: "checks", heroId: data.hero_id,
      verdict: "had more edge than the check typically shows",
      emptyText: "Not enough postflop checks with a clean line to grade yet.",
    });
  }

  // Sizing is a share of the pot, timing is seconds -- same comparison, two
  // units, so the formatter travels with the call.
  renderTellSection($("#hero-sizing", dash), data.sizing, {unit: "of pot"});
  renderTellSection($("#hero-timing", dash), data.timing, {
    unit: "to act", fmt: v => `${v.toFixed(1)}s`});

  const narrowing = $("#hero-narrowing", dash);
  if (!data.narrowing || !data.narrowing.length) {
    narrowing.innerHTML = `<div class="small muted">Not enough hands reaching
      each street yet.</div>`;
  } else {
    const chart = h("div", "narrow-wrap");
    chart.appendChild(narrowingChart(data.narrowing));
    narrowing.appendChild(chart);
    const strengths = data.narrowing.map(s => s.avg_strength);
    if (strengths.length >= 2) {
      const monotone = strengths.every((v, i) => i === 0 || v >= strengths[i - 1]);
      const note = h("p", "small muted");
      note.textContent = monotone
        ? "Narrows street by street, as a continuing range should."
        : "Does not narrow monotonically -- worth a look at which street gives it back.";
      narrowing.appendChild(note);
    }
  }
}
