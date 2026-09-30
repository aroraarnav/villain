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

  // The grid (what you played, hand by hand) beside the audit (which of those
  // were wrong, spot by spot): the grid shows a shape, the audit names hands.
  const rangeCols = h("div", "dash-cols wide");
  const gridCol = h("div", "col", `<div class="panel">
    <h2 id="hero-range-head">preflop range</h2>
    <div class="small muted" style="margin:-6px 0 10px">Cards known on ${fmtPct(data.visibility)} of ${data.hands} hands \u2014 only your own export shows this.</div>
    <div id="hero-grid"></div>
    <div class="range-legend"><span>never</span><span class="ramp"></span><span>always</span></div>
  </div>`);
  const spotCol = h("div", "col");
  spotCol.appendChild(preflopPanel({preflop: data.spots || [], hero_id: data.hero_id}));
  rangeCols.append(gridCol, spotCol);

  const gradesPanel = h("div", "panel wide", `
    <h2 id="hero-grades-head">fold grades</h2>
    <div id="hero-folds"></div>`);
  dash.appendChild(gradesPanel);
  dash.appendChild(rangeCols);

  const tellsPanel = h("div", "panel wide", `
    <h2 id="hero-tells-head">sizing tell</h2>
    <div id="hero-sizing"></div>`);
  dash.appendChild(tellsPanel);

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
    to be.`));
  $("#hero-tells-head", dash).appendChild(info(
    `Does your bet size change with the hand behind it? Nobody's hand
    strength is known often enough to ask a villain this -- yours is known on
    every bet, not just the ones that reached showdown.`));

  $("#hero-grid", dash).appendChild(rangeGrid(data.grid));

  if (data.grade_error) {
    $("#hero-folds", dash).innerHTML = `<div class="small muted">${esc(data.grade_error)}</div>`;
  } else {
    renderGradedSection($("#hero-folds", dash), data.fold_grades, {
      noun: "folds", heroId: data.hero_id,
      verdict: "had more edge than the bet typically shows",
      emptyText: "Not enough postflop folds with a clean line to grade yet.",
    });
  }

  renderTellSection($("#hero-sizing", dash), data.sizing, {unit: "of pot"});
}
