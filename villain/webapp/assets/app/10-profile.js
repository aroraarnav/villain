/* ---- shared renderers ---- */
function rosterTable(players, opts) {
  const wrap = h("div", "scroller");
  // GTO dropped as a default column: a bare 0-100 integer beside the skill
  // bar reads as a second, differently-encoded skill score. It is still one
  // click away, on the profile it links to.
  wrap.innerHTML = `<table><thead><tr>
      <th data-k="name">player</th>
      <th data-k="hands" class="num">hands</th><th data-k="archetype">read</th>
      <th data-k="skill" class="num">skill</th>
      <th data-k="exploitability" class="num">worth bb/100</th>
      <th data-k="top_leak">biggest leak</th>
    </tr></thead><tbody></tbody></table>`;
  const body = $("tbody", wrap);
  let sort = {key: "skill", dir: -1};
  function draw() {
    const rows = [...players].sort((a, b) => {
      let x = a[sort.key], y = b[sort.key];
      if (x == null) x = -Infinity;
      if (y == null) y = -Infinity;
      const cmp = (typeof x === "number" && typeof y === "number")
        ? x - y : String(x).localeCompare(String(y));
      return cmp * sort.dir;
    });
    body.innerHTML = "";
    for (const p of rows) {
      const tr = document.createElement("tr");
      const isHero = p.is_hero || (opts && opts.heroId != null && p.player_id === opts.heroId);
      if (isHero) tr.className = "hero-row-marker hero-scope";
      if (opts && opts.onClick && p.player_id != null) {
        tr.className = (tr.className ? tr.className + " " : "") + "clickable";
        tr.tabIndex = 0;
        tr.setAttribute("role", "button");
        tr.onkeydown = e => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); opts.onClick(p); }
        };
        tr.onclick = () => opts.onClick(p);
      }
      const shown = (p.session_names && p.session_names.length && p.db_name)
        ? p.session_names.join(" / ") : p.name;
      const linkBits = [];
      if (p.db_name) linkBits.push(`database: ${esc(p.db_name)}`);
      else if (p.session_names && p.session_names.length)
        linkBits.push(`as ${p.session_names.map(n => `\u201c${esc(n)}\u201d`).join(", ")}`);
      tr.innerHTML = `
        <td><span class="name">${esc(shown)}</span>${
            isHero ? '<span class="tag hero-tag">you</span>' : ""}
            ${linkBits.length
              ? `<div class="small muted">${linkBits.join(" \u00b7 ")}</div>` : ""}</td>
        <td class="num">${p.hands}</td>
        <td><span class="tag arch ${p.confidence >= 0.5 ? "on" : ""}">${esc(p.archetype)}</span></td>
        <td class="num"></td>
        <td class="num worth${p.exploitability > 10 ? " big" : ""}">${
          p.exploitability ? p.exploitability.toFixed(1) : "\u2014"}</td>
        <td class="small leakcell">${p.top_leak ? esc(p.top_leak)
          : '<span class="muted">nothing yet</span>'}</td>`;
      const holder = document.createElement("div");
      holder.style.cssText = "display:flex;gap:8px;align-items:center;justify-content:flex-end";
      const label = document.createElement("span");
      if (p.skill == null || p.skill_tier === "unknown") {
        label.className = "muted";
        label.textContent = "\u2014";
        bindTip(holder, `<b>unknown</b><br>${termTip("unknown")}`);
        holder.append(label);
      } else {
        label.textContent = p.skill.toFixed(0);
        // Mapped to the observed domain, not 0-100: anchored at zero the whole
        // roster looked equally full -- p10 to p90 differed by 14px of bar.
        holder.append(bar(Math.max(0, p.skill - 40), 55, "var(--mark-3)", 66), label);
        bindTip(holder, `<b>${esc(p.skill_tier)}</b> ${p.skill.toFixed(0)}/100<br>
          ${termTip("confidence")} ${fmtPct(p.skill_confidence)}`);
      }
      // Sample quality moved onto the hands count as a tooltip: it qualifies
      // that number and nothing else, so it does not need its own line on
      // every row.
      const hcell = tr.children[1];
      if (hcell) {
        hcell.classList.add("q-" + String(p.sample_quality).split(" ")[0]);
        bindTip(hcell, `<b>${esc(p.sample_quality)}</b><br>${termTip(p.sample_quality)}`);
      }
      // Confidence moves into the pill's own tooltip -- dashed vs solid
      // already carries the fact that it's uncertain; the number is one
      // hover away rather than a second line on every row of the roster.
      const archTag = $(".tag.arch", tr);
      if (archTag) bindTip(archTag, `${termTip("confidence")}<br><br>
        ${fmtPct(p.confidence)} sure`);
      /* An unconfirmed read still belongs in the column -- "none clears the
         bar" left the weakest player at the table looking like the safest.
         The marker says which kind of claim it is. */
      if (p.top_leak && p.top_leak_status !== "confirmed") {
        const flag = h("span", "flag");
        flag.textContent = "!";
        bindTip(flag, `<span class="hl">${p.top_leak_status === "watch"
          ? "not confirmed" : "from the rating, not a frequency"}</span><br>
          ${esc(p.top_leak_note)}`);
        $(".leakcell", tr).appendChild(flag);
      } else if (p.top_leak) {
        $(".leakcell", tr).appendChild(info(esc(p.top_leak_note)));
      }
      tr.children[3].appendChild(holder);
      body.appendChild(tr);
    }
    wrap.querySelectorAll("th").forEach(th => {
      th.classList.toggle("sorted", th.dataset.k === sort.key);
      th.classList.toggle("desc", th.dataset.k === sort.key && sort.dir < 0);
    });
  }
  wrap.querySelectorAll("th").forEach(th => th.onclick = () => {
    const k = th.dataset.k;
    sort = {key: k, dir: sort.key === k ? -sort.dir : (k === "name" ? 1 : -1)};
    draw();
  });
  draw();
  return wrap;
}

/* vs GTO -- how close a player's frequencies sit to an optimal baseline, with
   one rating. Preflop rows are a solver reference (exact as category
   frequencies), postflop are board-averaged benchmarks; the fidelity rides
   every row and the tooltip says which is which.

   Rendered as a rating badge plus a link into every row, rather than a full
   panel of eight rows sitting beside Key numbers -- the same idea (a
   frequency next to a baseline) with less certainty drawn, taking a whole
   peer tile on the one screen meant to be read mid-hand. */
function gtoRows(rows) {
  const host = h("div", "gto-rows");
  for (const r of rows) {
    const dir = r.deviation > 0 ? "+" : "−";
    const row = h("div", "gto-row", `
      <span class="gto-name">${esc(statLabel(r.stat, null))}<span
        class="gto-fid ${r.fidelity === "solver" ? "solver" : ""}">${
        r.fidelity === "solver" ? "solver" : "bench"}</span></span>
      <span class="gto-nums small muted">you ${fmtPct(r.player)} · gto ${fmtPct(r.target)}</span>
      <span class="gto-dev">${dir}${Math.round(Math.abs(r.deviation) * 100)}</span>`);
    host.appendChild(row);
  }
  return host;
}

function gtoExplainer() {
  return `<span class="hl">GTO rating</span><br>
    How close these frequencies sit to a game-theory-optimal baseline — the
    part of a game a perfect opponent still could not exploit.<br><br>
    <b>Preflop</b> is a solver reference at 100bb, exact as category
    frequencies.<br><b>Postflop</b> is an equilibrium benchmark, board-averaged
    — not a live solver. Preflop rows weigh double in the score.`;
}

function openGtoModal(gto) {
  const modal = openSheet("vs GTO — every stat");
  $(".sheet", modal).appendChild(gtoRows(gto.rows));
}

/* A compact rating badge, `you N/100 GTO`, plus a link to the full row list --
   for a panel header, not a panel of its own. */
function renderGtoBadge(gto) {
  if (!gto || gto.rating == null || !(gto.rows || []).length) return null;
  const wrap = h("span", "small muted");
  wrap.style.cssText = "display:inline-flex;align-items:center;gap:6px";
  const badge = h("span", "gto-badge", `<b>${Math.round(gto.rating)}</b><span class="of">/100 GTO</span>`);
  badge.appendChild(info(gtoExplainer()));
  const link = h("button", "linkbtn");
  link.textContent = "See vs GTO";
  link.onclick = () => openGtoModal(gto);
  wrap.append(badge, link);
  return wrap;
}

/* Profile: one function per tile. `hero` changes the wording, not the shape. */

function profileHead(p, isHero, hero) {
  const head = h("div", "panel wide", `
    <div class="profile-head">
      <div class="profile-id">
        <div class="hero">${esc(p.name || p.archetype)}${
          isHero ? '<span class="hero-badge">you</span>' : ""}</div>
        <div class="read-meta" id="read-meta"></div>
        <div class="read-copy">
          <p class="summary">${esc(p.summary)}</p>
        </div>
      </div>
      <div class="profile-stats">
        <div class="stat-pair ring">
          <div class="skill-ring" id="skill-ring">
            <span class="score">${p.skill.measured === false ? "\u2014" : p.skill.score.toFixed(0)}</span>
          </div>
          <span class="k">${p.skill.measured === false ? "unknown" : "skill"}</span>
        </div>
        <div class="stat-pair" id="worth-stat">
          <span class="v">${p.skill.exploitability_bb100.toFixed(1)}</span>
          <span class="k">bb/100 available</span>
        </div>
      </div>
    </div>`);
  if (p.skill.measured !== false) {
    $("#skill-ring", head).prepend(skillGauge(p.skill.score));
  }
  $("#worth-stat .k", head).appendChild(info(termTip("available")));
  if (!hero && p.plan) {
    const planLink = h("button", "linkbtn how-link");
    planLink.textContent = "How to play them";
    planLink.onclick = () => {
      openSheet(`How to play ${esc(p.name || p.archetype)}`);
      $(".sheet").insertAdjacentHTML("beforeend",
        `<div class="how-body">${esc(p.plan)}</div>`);
    };
    $(".read-copy", head).appendChild(planLink);
  }

  const badge = $("#skill-ring", head);
  bindTip(badge, p.skill.measured === false
    ? `<b>unknown</b><br>${termTip("unknown")}`
    : `<b>${esc(p.skill.tier)}</b> ${p.skill.score.toFixed(0)}/100<br>
    <span class="muted">confidence ${fmtPct(p.skill.confidence)}</span>
    ${p.skill.observed_bb100 == null ? ""
      : `<br><span class="muted">${p.skill.observed_bb100.toFixed(1)} bb/100 observed</span>`}`);

  // The meta row is a flex row on a spacing step, so nothing in it carries a
  // hand-set margin any more.
  const meta = $("#read-meta", head);
  // The archetype now lives here as a pill, not as a subtitle under the name
  // plus a second sentence repeating it -- dashed means the read hasn't
  // cleared 50%, solid means it has, same convention the roster already uses.
  const archPill = h("span", "tag arch" + (p.archetype_confidence >= 0.5 ? " on" : ""));
  archPill.textContent = p.archetype;
  archPill.appendChild(info(`${termTip("confidence")}<br><br>
    ${fmtPct(p.archetype_confidence)} sure<br><br>
    <span class="hl">also plausibly</span><br>${
      p.archetype_mix.slice(1, 4).map(([n, v]) => `${esc(n)} ${fmtPct(v)}`).join("<br>")}`));
  const line = document.createElement("span");
  line.append(document.createTextNode(`${p.hands} hands`));
  line.appendChild(info(`<b>${esc(p.sample_quality)}</b><br>${termTip(p.sample_quality)}`));
  meta.append(archPill, line);
  // Who they are on the hands they played against you. Only here, never on
  // the roster: the roster is how everybody plays the field, and two
  // references in one list is how "tag" stopped meaning anything.
  if (p.versus) {
    const vs = h("span", "", `<span class="tag arch on">vs you: ${esc(p.versus.archetype)}</span>`);
    vs.appendChild(info(`<span class="hl">against you</span><br>
      On the ${p.versus.decisions.toLocaleString()} decisions they made with you
      on the other side, ${esc(p.versus.regime_label)}, they play like
      <b>${esc(p.versus.archetype)}</b> (${fmtPct(p.versus.confidence)} sure)
      &mdash; against <b>${esc(p.archetype)}</b> for the field.<br><br>
      <span class="hl">also plausibly</span><br>${
        p.versus.mix.slice(1, 3).map(m =>
          `${esc(m.archetype)} ${fmtPct(m.share)}`).join("<br>")}<br><br>
      One table size, never pooled: a player can be one thing against you
      heads-up and another six-handed, and the average of those describes
      neither table you sat at.`));
    meta.appendChild(vs);
  }
  if (p.contributions && Object.keys(p.contributions).length > 1) {
    const where = document.createElement("span");
    where.textContent = p.regime_label;
    where.appendChild(info(`<span class="hl">one read, several table sizes</span><br>
      Each table's hands are measured against that table's own norms, then
      pooled. Shown on ${esc(p.regime_label)} terms, where they play most.<br>
      ${esc(p.table_mix || "")}`));
    meta.appendChild(where);
  }

  return head;
}


function skillPanel(p) {
  const skillBox = h("div", "panel wide p-skill");
  skillBox.innerHTML =
    `<div class="spread"><h2>Skill breakdown <span class="muted" style="font-weight:400">\u00b7 ` +
    `${esc(p.skill.tier)}</span></h2></div>` +
    `<div class="skill-side two-up" id="skill-side"></div>`;
  const gtoBadge = renderGtoBadge(p.gto);
  if (gtoBadge) $(".spread", skillBox).appendChild(gtoBadge);
  const skillSide = $("#skill-side", skillBox);
  skillSide.innerHTML = "";
  const comps = p.skill_components || p.skill.components;
  for (const c of [...comps].sort((a, b) => a.score - b.score)) {
    const row = h("div", "comp" + (c.weak ? " weak" : ""), `<span class="comp-name">${esc(c.name)}</span>
      <span class="comp-bar"></span>
      <span class="comp-score">${c.score.toFixed(0)}</span>`);
    // Stretched to the cell rather than scaled into it: with the aspect ratio
    // preserved a 999-wide bar in a 250px column drew a 3px hairline.
    const compBar = bar(c.score, 100, c.weak ? "var(--warn)" : "var(--mark-1)", 999);
    compBar.setAttribute("preserveAspectRatio", "none");
    $(".comp-bar", row).appendChild(compBar);
    // The sentence, the figure it rests on, and what it means all live in the
    // tooltip now, so the breakdown reads as a scorecard, not a wall of prose.
    const bits = [`<b>${esc(c.name)}</b>`];
    if (c.measures) bits.push(esc(c.measures));
    if (c.note) bits.push(`<span class="hl">${esc(c.note)}</span>`);
    if (c.meaning) bits.push(esc(c.meaning));
    bits.push(`<span class="muted">counts ${c.weight}x toward the rating</span>`);
    $(".comp-name", row).appendChild(info(bits.join("<br>")));
    skillSide.appendChild(row);
  }

  return skillBox;
}


function whatToDoTile(p, hero, leaks) {
  /* The tile the screen exists for, and the only one on the primary surface:
     priced leaks, then the unconfirmed watchlist, then the synthesis row. */
  const doBox = h("div", "panel wide primary p-do", `<h2>${hero ? "Your biggest leaks" : "What to do"}</h2>
    <div class="leaks"></div>`);

  const leakBox = $(".leaks", doBox);
  if (!leaks.length) {
    const nothing = (p.watchlist || []).length || (p.weak_spots || []).length
      ? `<div class="empty">Nothing clears the evidence bar yet. Below is
         what the numbers point at so far.</div>`
      : `<div class="empty">Nothing stands out yet. Play them straight and
         collect more hands.</div>`;
    leakBox.innerHTML = nothing;
  }
  for (const l of leaks) {
    const div = h("div", `leak priced t-${esc(l.tier)}`);
    // The glance view gets the first clause -- ranked by bb/100, this is the
    // column that should read as a scorecard, not a stack of essays. The full
    // sentence is never dropped, only moved into "Why, and what not to do".
    const doFirst = l.do ? l.do.split(/\.\s+/)[0].replace(/\.+$/, "") + "." : "";
    const doTruncated = !!(l.do && doFirst.length < l.do.length);
    // One decimal on the glance surface, two only in the numbers line beside
    // the sample it rests on. A leak under 0.1 prints as "<0.1" rather than
    // "0.00": a list sorted by price should never show a price of zero, and
    // rounding a real figure to nothing is the one thing worse than the extra
    // digit.
    const thin = l.severity_bb100 < 0.1;
    div.innerHTML = `
      <div class="leak-price${thin ? " thin" : ""}">
        <span class="v">${thin ? "&lt;0.1" : l.severity_bb100.toFixed(1)}</span>
        <span class="u">bb/100</span>
      </div>
      <div class="leak-body">
        <div class="headline"><b>${esc(l.headline)}</b>
          <span class="tag tier ${esc(l.tier)}">${esc(l.tier)}</span></div>
        ${hero ? "" : `<div class="leak-advice">${esc(doFirst)}</div>`}
        <div class="small muted numbers"></div>
      </div>`;
    bindTip($(".leak-price", div), `<span class="hl">${
      l.severity_bb100.toFixed(2)} bb/100</span><br>${termTip("available")}`);
    $(".tier", div).after(info(`${termTip(l.tier)}<br><br>${esc(l.priority)}`));

    const numbers = $(".numbers", div);
    numbers.appendChild(document.createTextNode(
      `${fmtPct(l.value)} vs ${fmtPct(l.breakeven)} break-even \u00b7 `));
    if (p.player_id != null) {
      const link = h("button", "linkbtn");
      link.textContent = `${Math.round(l.sample)} hands`;
      link.title = "show the hands behind this";
      link.onclick = () => showEvidence(p.player_id, l.stat, l.headline);
      numbers.appendChild(link);
    } else {
      numbers.appendChild(document.createTextNode(`${Math.round(l.sample)} hands`));
    }
    numbers.appendChild(info(statTip(l.stat, l.headline)));

    // A popup, not an inline <details>: expanding it in place grew this panel
    // after the columns were balanced, which is what threw the layout off. Same
    // sheet the hands open in. "Do" only appears here when the glance view cut
    // it short -- otherwise the one sentence already on screen is the whole of it.
    const whydont = [doTruncated ? ["Do", l.do] : null, ["Why", l.why], ["Do not", l.dont]]
      .filter(Boolean).filter(([, t]) => t);
    // On the evidence line rather than a line of its own. Five leaks with a
    // disclosure each was five extra rows of height on the one panel that has
    // to fit above the fold, and the link belongs with the rest of the
    // "where this came from" apparatus anyway.
    if (!hero && whydont.length) {
      numbers.appendChild(document.createTextNode(" · "));
      const link = h("button", "linkbtn how-link");
      link.textContent = "Why, and what not to do";
      link.onclick = () => {
        const modal = openSheet(esc(l.headline));
        $(".sheet", modal).insertAdjacentHTML("beforeend", `<div class="how-body"></div>`);
        const how = $(".how-body", modal);
        for (const [label, text] of whydont) {
          const block = h("div", "howblock", `<div class="howlabel">${esc(label)}</div>
            <div>${esc(text)}</div>`);
          how.appendChild(block);
        }
      };
      numbers.appendChild(link);
    }
    leakBox.appendChild(div);
  }

  // Same row shape as a priced leak, because it is the same kind of claim one
  // step short of the evidence bar -- but the left cell holds the confidence
  // rather than a price, and the rail is dashed, so it never reads as another
  // number in the ranking.
  for (const w of (p.watchlist || [])) {
    const div = h("div", "leak priced watch", `
      <div class="leak-price thin">
        <span class="v">${fmtPct(w.confidence)}</span>
        <span class="u">sure</span>
      </div>
      <div class="leak-body">
        <div class="headline"><b>${esc(w.headline)}</b>
          <span class="tag tier">watch</span></div>
      </div>`);
    $(".tier", div).after(info(`${termTip("watch")}<br><br>${esc(w.in_words)}`));
    leakBox.appendChild(div);
  }


  // Not a priced row -- a synthesis of the rows above it -- so it stops
  // borrowing their shape: no price cell, a recessed ground, and it sits at
  // the foot of the list where a summary belongs.
  for (const c of (p.combinations || [])) {
    const block = h("div", "leak compound", `<div class="headline"><b>${esc(c.headline)}</b>
      <span class="tag">these compound</span></div>
      <div class="leak-advice">${esc(c.body)}</div>`);
    leakBox.appendChild(block);
  }

  return doBox;
}


function adjustmentsTile(p) {
  /* How they play *you*, or null. Most players against most opponents have no
     adjustment, and an empty panel takes a column off a screen meant to be
     read mid-hand. */
  const adjustments = p.adjustments || [];
  if (adjustments.length) {
    const adjBox = document.createElement("div");
    // Full width, below the two columns rather than inside the height-packed
    // masonry: a short reads-only panel could never balance against the tall
    // skill breakdown without leaving dead space on one side or the other.
    adjBox.className = "panel wide adjust hero-scope";
    adjBox.innerHTML = `<h2>Against you<span class="hero-badge">hero</span></h2>
      <div class="panel-lead">
        Measured against how they play everybody else, not against the
        field.</div>`;
    // The mark rides the title itself, like every other panel header (see the
    // hero dashboard): .info's own 5px offset places it, no wrapper needed.
    $("h2", adjBox).appendChild(info(termTip("adjustment")));
    const adjGrid = h("div", "adjust-grid");
    adjBox.appendChild(adjGrid);
    for (const a of adjustments) {
      const div = h("div", "leak", `
        <div class="leak-head">
          <div class="headline"><b>${esc(a.behavior)}</b>${
            a.regime_label ? ` <span class="tag">${esc(a.regime_label)}</span>` : ""}</div>
          <div class="num small muted">${fmtPct(Math.min(a.confidence, 0.99))} sure</div>
        </div>
        <div class="small muted numbers"></div>`);
      for (const [label, value, color, term] of [
            ["against you", a.versus, "var(--mark-3)", "against you"],
            ["otherwise", a.baseline, "var(--mark-1)", "otherwise"]]) {
        const row = h("div", "metric");
        const name = h("span", "small"); name.textContent = label;
        const val = h("span", "small muted"); val.style.textAlign = "right";
        val.textContent = fmtPct(value);
        row.append(name, bar(value, 1, color, 150), val);
        bindTip(row, termTip(term));
        div.insertBefore(row, $(".numbers", div));
      }
      const numbers = $(".numbers", div);
      const seen = `${Math.round(a.sample)} against you`;
      if (p.player_id != null) {
        // Opens on the against-you slice rather than its parent, so the hands
        // shown are the ones the read is actually about.
        const link = h("button", "linkbtn");
        link.textContent = seen;
        link.title = "show the hands behind this";
        link.onclick = () => showEvidence(p.player_id, a.evidence_stat, a.behavior);
        numbers.appendChild(link);
      } else {
        numbers.appendChild(document.createTextNode(seen));
      }
      numbers.appendChild(document.createTextNode(
        ` · ${Math.round(a.baseline_sample)} against everybody else`));
      numbers.appendChild(info(statTip(a.stat, a.behavior)));
      adjGrid.appendChild(div);
    }
    return adjBox;
  }
  return null;
}


function timingTellsTile(p) {
  /* Null unless some cell actually has a tell -- a grid of "not enough data"
     is noise wearing a panel's clothes. */
  const tells = p.timing_tells || [];
  // Render only when some cell actually has a tell -- a grid of "not enough
  // data" is noise wearing a panel's clothes.
  if (tells.some(c => c.n >= 5 && !/no clear tell|not enough/i.test(c.label || ""))) {
    const timingBox = h("div", "panel wide");
    const headRow = h("div", "spread");
    const title = h("div", "headline");
    const h2 = document.createElement("h2");
    h2.style.margin = "0";
    h2.textContent = "Timing tells";
    const flag = h("span", "flag");
    flag.textContent = "!";
    bindTip(flag, `<span class="hl">use with caution</span><br>
      Timing is noisy online. Each cell is the <em>share</em> of that action
      at this pace, plus whether they won / went to showdown / folded next
      <em>differently</em> than after the same action at normal pace. Use it
      to break ties \u2014 never as the whole basis of a decision.`);
    title.append(h2, flag);
    headRow.appendChild(title);
    const note = h("span", "small muted");
    note.textContent = "share of action + outcome vs normal pace";
    headRow.appendChild(note);
    timingBox.appendChild(headRow);

    const byKey = Object.fromEntries(
      tells.map(c => [`${c.pace}:${c.street}:${c.action}`, c]));
    for (const street of ["flop", "turn"]) {
      const block = h("div", "timing-street", `<div class="street-label">${street}</div>`);
      const grid = h("div", "timing-grid", `<div class="corner"></div>
        <div class="colhead">check</div>
        <div class="colhead">call</div>
        <div class="colhead">raise</div>`);
      for (const pace of ["snap", "tank"]) {
        const rowhead = h("div", "rowhead");
        rowhead.textContent = pace;
        grid.appendChild(rowhead);
        for (const action of ["check", "call", "aggro"]) {
          const cell = byKey[`${pace}:${street}:${action}`] || {
            n: 0, total: 0, share: null, label: "Not enough data",
            read: "Need more timed actions."};
          const div = document.createElement("div");
          const quiet = cell.n < 5 || /no clear tell|not enough/i.test(cell.label || "");
          div.className = "timing-cell" + (quiet ? " thin" : " on");
          const share = cell.share == null ? ""
            : `${Math.round(100 * cell.share)}% of ${cell.total}`;
          const nLine = cell.n
            ? `${cell.n} timed${share ? ` \u00b7 ${share}` : ""}`
            : "no data yet";
          // Only a real tell prints a label; everything else is a quiet dash.
          // The read that used to crowd every cell is one hover away.
          div.innerHTML = quiet
            ? `<div class="tell">\u2014</div>`
            : `<div class="tell">${esc(cell.label)}</div><div class="n">${cell.n} timed</div>`;
          bindTip(div, `<b>${esc(cell.label)}</b><br>${esc(cell.read)}<br>
            <span class="muted">${esc(nLine)}</span>`);
          grid.appendChild(div);
        }
      }
      block.appendChild(grid);
      timingBox.appendChild(block);
    }
    return timingBox;
  }
  return null;
}


function keyNumbersTile(p, hero) {
  /* The HUD line: the six every tracker prints, in the order they print them,
     because that order is what a player's eye already knows. */
  // The HUD line: the six every tracker prints, in the order they print them,
  // because that order is what a player's eye already knows. Cutting this to
  // three rows of a table did not make the screen calmer -- it left a column
  // 300px empty beside a full one. Six figures in one strip is more numbers in
  // less height than three rows were, and it reads as an instrument.
  const HUD = [["vpip", "VPIP"], ["pfr", "PFR"], ["three_bet", "3-bet"],
               ["fold_to_three_bet", "Fold to 3B"], ["cbet:flop", "C-bet flop"],
               ["fold_vs_bet:flop", "Fold v bet F"]];
  const TIMING = /^(tank_fold|snap_call)(:|$)/;
  const byStat = Object.fromEntries(p.rows.map((r) => [r.stat, r]));
  const ordered = [...p.rows]
    .filter((r) => !TIMING.test(r.stat))
    .sort((a, b) => {
      const rank = (r) => {
        const i = HUD.findIndex(([s]) => s === r.stat);
        return i === -1 ? HUD.length : i;
      };
      return rank(a) - rank(b);
    });

  const makeTable = () => {
    const table = h("div", "scroller", `<table><thead><tr><th>stat</th>
        <th style="width:40%">0% \u2014 100%</th>
        <th class="num">estimate</th><th class="num">sample</th></tr></thead>
      <tbody></tbody></table>`);
    return table;
  };
  const fill = (table, rows) => {
    const tbody = $("tbody", table);
    for (const row of rows) {
      const tr = h("tr", "", `<td class="label"></td><td></td>
                      <td class="num">${fmtPct(row.value)}</td>
                      <td class="num small muted">${Math.round(row.opps)}</td>`);
      const label = $(".label", tr);
      label.appendChild(document.createTextNode(row.label));
      label.appendChild(info(statTip(row.stat, row.label, row)));
      tr.children[1].appendChild(statRow(row));
      tbody.appendChild(tr);
    }
  };

  const hudBox = h("div", "panel wide p-hud", `<div class="spread"><h2>Key numbers</h2>
    <span class="small muted hud-actions"></span></div><div class="hud"></div>`);
  const hudHost = $(".hud", hudBox);
  for (const [stat, label] of HUD) {
    const row = byStat[stat];
    const cell = h("div", "hud-cell" + (row ? "" : " thin"));
    // The percent sign is a unit, not a digit: at full weight beside a 21px
    // figure it competes with the number it is qualifying.
    cell.innerHTML = `<div class="k">${esc(label)}</div>
      <div class="v">${row ? `${(100 * row.value).toFixed(0)}<span class="pc">%</span>`
                           : "—"}</div>`;
    if (row) {
      cell.appendChild(sparkRow(row));
      // One hover gives what the strip cannot: what the figure counts, the
      // interval around it, the sample under it, and the threshold. Composed
      // here rather than concatenating the two existing tip builders, which
      // between them would print the label and the field reading twice.
      bindTip(cell, `${statTip(row.stat, row.label, row)}
        <div class="dir"><span class="muted">95% range ${fmtPct(row.lo)}–${fmtPct(row.hi)}
        · ${row.opps} ${esc(row.denominator)} · field ${fmtPct(row.population)}</span></div>${
        row.breakeven == null ? ""
          : `<div class="dir" style="color:var(--warn)">${
             esc(row.breakeven_label)} ${fmtPct(row.breakeven)}</div>`}`);
    } else {
      bindTip(cell, `<b>${esc(label)}</b><br>No sample for this yet.`);
    }
    hudHost.appendChild(cell);
  }
  if (ordered.length) {
    // The rest open in the sheet popup rather than an inline <details>, so the
    // strip never changes height after it is drawn.
    const link = h("button", "linkbtn");
    link.textContent = `See all ${ordered.length} numbers`;
    link.onclick = () => {
      _heroVoice = hero;
      const modal = openSheet("Key numbers");
      $(".sheet", modal).insertAdjacentHTML("beforeend", `<div class="modal-numbers"></div>`);
      const full = makeTable();
      fill(full, ordered);
      $(".modal-numbers", modal).appendChild(full);
    };
    $(".hud-actions", hudBox).appendChild(link);
  }
  return hudBox;
}


function profileCard(p, opts) {
  opts = opts || {};
  const isHero = p.is_hero || (opts.heroId != null && p.player_id === opts.heroId);
  const hero = opts.hero || isHero;
  _heroVoice = hero;                     // glossary tooltips read in your voice
  const card = h("div", isHero ? "dash hero-scope" : "dash");
  const leaks = p.leaks;
  // Full-width tiles are placed last: dropped mid-grid they force a row break
  // and strand whatever tile precedes them alone on a line.
  const wideTiles = [];

  const head = profileHead(p, isHero, hero);
  card.appendChild(head);

  const doBox = whatToDoTile(p, hero, leaks);
  card.appendChild(doBox);

  for (const tile of [adjustmentsTile(p), timingTellsTile(p)]) {
    if (tile) wideTiles.push(tile);
  }

  card.appendChild(skillPanel(p));
  for (const tile of wideTiles) card.appendChild(tile);

  // Between the header band and What to do: the six are reference you read
  // before the plan, not after it.
  card.insertBefore(keyNumbersTile(p, hero), doBox);
  return card;
}

/* ---- a strip of player tabs over one profile at a time ---- */
function playerTabs(profiles, container, opts) {
  container.innerHTML = "";
  if (!profiles.length) {
    container.innerHTML = `<div class="panel"><div class="empty">No profiles.</div></div>`;
    return;
  }
  if (profiles.length === 1) {          // a strip of one is just clutter
    container.appendChild(profileCard(profiles[0], opts));
    return;
  }
  const strip = h("div", "ptabs");
  const body = document.createElement("div");
  container.append(strip, body);

  let current = 0;
  function show(i) {
    current = i;
    [...strip.children].forEach((b, j) => b.classList.toggle("on", i === j));
    body.innerHTML = "";
    body.appendChild(profileCard(profiles[i], opts));
  }
  profiles.forEach((p, i) => {
    const b = h("button", "ptab");
    const shown = (p.session_names && p.session_names.length && p.db_name)
      ? p.session_names.join(" / ") : p.name;
    const meta = [esc(p.regime_label), `${p.hands}h`];
    if (p.db_name) meta.push(`database: ${esc(p.db_name)}`);
    b.innerHTML = `<span>${esc(shown)}</span>
      <span class="meta">${meta.join(" \u00b7 ")}</span>`;
    b.onclick = () => show(i);
    strip.appendChild(b);
  });
  show(0);
}
