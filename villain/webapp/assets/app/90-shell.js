/* ---- tabs ---- */
function switchTab(tab, playerId) {
  // A blocking operation owns the window until it finishes. Leaving mid-import
  // replaces the view it is still writing into, and the veil it put up is
  // cleared by whatever it was doing rather than by the tab that replaced it --
  // which left a full-screen dim with nothing on it.
  if (document.querySelector(".veil.busy")) return;
  if (state.tab === "play" && tab !== "play") holdSimClock();
  state.tab = tab;
  // Bare "go to the database tab" clears which player was open; a link that
  // names a specific player (from Sessions, say) opens straight to them
  // instead of dropping back to the general roster.
  if (tab === "players") state.player = playerId != null ? playerId : null;
  document.querySelectorAll("nav button").forEach(b =>
    b.classList.toggle("on", b.dataset.tab === tab));
  $("#meta").textContent = "";
  renderWithSpinner();
}

/* In the browser the whole tool runs on this thread, so the work a tab does to
   build itself is work the page cannot paint through. Put a spinner up, give
   the browser a frame to actually draw it, and only then start. Two frames,
   because one only schedules the paint -- the second runs after it. A hidden
   tab never delivers those frames, so waiting for them is how a switch to
   another tab froze the load until you came back. */
const nextFrame = () => new Promise((done) => {
  if (document.visibilityState === "hidden") {
    done();
    return;
  }
  let settled = false;
  const finish = () => {
    if (settled) return;
    settled = true;
    document.removeEventListener("visibilitychange", onHide);
    done();
  };
  const onHide = () => {
    if (document.visibilityState === "hidden") finish();
  };
  document.addEventListener("visibilitychange", onHide);
  requestAnimationFrame(() => requestAnimationFrame(finish));
});

async function renderWithSpinner() {
  const view = $("#view");
  if (view) {
    view.innerHTML = `<div class="panel loading">
      <div class="spinner" aria-hidden="true"></div>
      <span>Loading\u2026</span></div>`;
    await nextFrame();
  }
  return render();
}

/* True while `tab` is the one on screen.
 *
 * Views build themselves asynchronously and then write. A view that started
 * before the reader switched away must not write at all -- correcting it
 * afterwards leaves a visible flash of the wrong screen. */
function onScreen(tab) { return state.tab === tab; }

/* Which render is the current one.
 *
 * Every tab builds itself asynchronously, and a slow one -- Database on a big
 * roster, Hero on a cold cache -- finishes long after the reader has moved on,
 * writing its screen over whatever they switched to. The simulator had this
 * and was fixed in place; the same thing was true of all of them, so the guard
 * belongs here rather than in each view.
 *
 * A render that is no longer the current one throws its output away, and the
 * tab that *is* current is drawn instead.
 */
let renderSeq = 0;

async function render() {
  const mine = ++renderSeq;
  const forTab = state.tab;
  try {
    if (!state.glossary) state.glossary = await get("/api/glossary");
    if (mine !== renderSeq) return;          // superseded while we waited
    if (state.tab === "session") viewSession();
    else if (state.tab === "sessions") await viewSessions();
    else if (state.tab === "hero") await viewHero();
    else if (state.tab === "play") await viewPlay();
    else await viewPlayers();
  } catch (err) {
    if (mine !== renderSeq) return;
    $("#view").innerHTML = `<div class="panel err">${esc(err.message)}</div>`;
    return;
  }
  // Finished into a tab nobody is looking at any more: draw the real one.
  if (mine === renderSeq && state.tab !== forTab) render();
}
document.querySelectorAll("nav button").forEach(b =>
  b.onclick = () => {
    if (b.disabled) return;
    if (document.querySelector(".veil.busy")) {
      // Nudge the thing that is holding the window, so the click reads as
      // "not now" rather than as nothing happening at all.
      const sheet = document.querySelector(".busy-sheet");
      if (sheet) {
        sheet.animate(
          [{ transform: "translateX(0)" }, { transform: "translateX(-4px)" },
           { transform: "translateX(4px)" }, { transform: "translateX(0)" }],
          { duration: 180 });
      }
      return;
    }
    switchTab(b.dataset.tab);
  });

/* ---- tabs with nothing to show yet ----
   Hero needs a hand history with your own cards in it; Simulate needs somebody
   measured enough to play against. Both are ordinary on a new database, and
   both used to be found out by clicking, reading an explanation and clicking
   back. The server says which are ready and why not, and the reason sits on
   the tab itself. */
async function paintTabs() {
  let tabs;
  try {
    tabs = (await get("/api/meta")).tabs || {};
  } catch {
    return;                     // never let this stop the interface loading
  }
  for (const button of document.querySelectorAll("nav button")) {
    const state_ = tabs[button.dataset.tab];
    const blocked = state_ && state_.ok === false;
    button.disabled = !!blocked;
    button.classList.toggle("off", !!blocked);
    if (blocked) button.title = state_.why || "";
    else button.removeAttribute("title");
  }
  // Landing on a tab that has since become unavailable -- a reset, say --
  // would otherwise leave the view stranded on a dead screen.
  const here = document.querySelector(`nav button[data-tab="${state.tab}"]`);
  if (here && here.disabled) switchTab("players");
}
paintTabs();

/* Sun when it is dark (click for light), moon when it is light. The icon
   shows what you get, not what you have. */
const SUN = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none"
  stroke="currentColor" stroke-width="2" stroke-linecap="round">
  <circle cx="12" cy="12" r="4.2"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2
  M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/></svg>`;
const MOON = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none"
  stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/></svg>`;
const themeBtn = $("#theme");
function isDark() {
  const set = document.documentElement.getAttribute("data-theme");
  if (set) return set === "dark";
  return matchMedia("(prefers-color-scheme: dark)").matches;
}
function paintTheme() { themeBtn.innerHTML = isDark() ? SUN : MOON; }
themeBtn.onclick = () => {
  document.documentElement.setAttribute("data-theme", isDark() ? "light" : "dark");
  paintTheme();
};
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", paintTheme);
paintTheme();

document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  for (const id of ["#modal2", "#modal"]) {
    const layer = $(id);
    if (layer && layer.innerHTML.trim()) { layer.innerHTML = ""; return; }
  }
});

document.addEventListener("visibilitychange", () => {
  if (!state.game || state.analysis) return;
  if (document.visibilityState === "hidden") {
    holdSimClock();
    return;
  }
  if (state.tab === "play" && !state.paused) {
    thawSimClock();
    armSimClock();
    paintDealCount();
  }
});

renderWithSpinner();
