/**
 * Sign-in, and one private database per account, on Supabase.
 *
 * Two things shape everything here.
 *
 * The database is bigger than the free tier will take in one piece: 50 MB per
 * file, against a 98 MB database that gzips to 60. So it is gzipped and cut
 * into chunks, uploaded as separate objects, and stitched back together on the
 * way in. Chunks are named under a *version*, and the manifest naming that
 * version is written last -- so a half-finished upload leaves the previous
 * version intact and readable rather than a corrupted mixture of both.
 *
 * And moving 60 MB is worth avoiding. The working copy already lives in this
 * browser through Pyodide's IDBFS, so the ordinary case -- same laptop, nothing
 * changed elsewhere -- reads one small manifest, sees the version it already
 * has, and transfers nothing.
 */
window.VillainSync = (() => {
  const cfg = window.VILLAIN_SUPABASE || {};
  const enabled = !!(cfg.url && cfg.anonKey);
  const BUCKET = "dbs";

  // Comfortably under the free tier's 50 MB ceiling, so a chunk cannot be
  // refused for being a few bytes over after gzip.
  //
  // Overridable because the multi-part path is otherwise only reachable with a
  // database of tens of thousands of hands: setting `chunkBytes` small in
  // web/config.js exercises splitting, the manifest and reassembly against the
  // real service with an import that takes seconds. Not a tuning knob -- leave
  // it unset in anything anyone else will load.
  const CHUNK = Number(cfg.chunkBytes) > 0 ? Number(cfg.chunkBytes) : 40 * 1024 * 1024;

  const client = enabled
    ? window.supabase.createClient(cfg.url, cfg.anonKey, {
        auth: {
          persistSession: true,
          detectSessionInUrl: true,
          // Implicit, not PKCE. PKCE keeps the code verifier in the browser
          // that *asked* for the link, so opening the email on a phone fails --
          // which is precisely what an account is meant to make possible.
          flowType: "implicit",
        },
      })
    : null;

  const store = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* private mode */ } },
    drop(key) { try { localStorage.removeItem(key); } catch { /* nothing to clear */ } },
  };

  const gzip = async (bytes) =>
    new Uint8Array(await new Response(
      new Blob([bytes]).stream().pipeThrough(new CompressionStream("gzip"))).arrayBuffer());

  const gunzip = async (bytes) =>
    new Uint8Array(await new Response(
      new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"))).arrayBuffer());

  const bucket = () => client.storage.from(BUCKET);

  /**
   * Delete every stored version of a file except the one named.
   *
   * Storage has no directories: a "version folder" is a name prefix, and
   * listing with that prefix is how you find out what is still there. Called
   * after every write, so orphans from an interrupted save are cleared on the
   * next one rather than living forever.
   */
  async function sweep(uid, file, keep) {
    const { data: versions, error } = await bucket().list(`${uid}/${file}`, { limit: 1000 });
    if (error || !versions) return;
    const doomed = [];
    for (const entry of versions) {
      // Objects at this level (current.json) have metadata; prefixes do not.
      if (!entry.name || entry.name === keep || entry.metadata) continue;
      const { data: parts } = await bucket().list(`${uid}/${file}/${entry.name}`, { limit: 1000 });
      for (const part of parts || []) doomed.push(`${uid}/${file}/${entry.name}/${part.name}`);
    }
    if (doomed.length) await bucket().remove(doomed);
  }

  /**
   * Remove a file from storage completely -- every version, and the manifest.
   *
   * Needed because sweeping only ever runs while writing. A file that stops
   * existing locally -- the hero cache after a reset -- would otherwise never
   * be written again, and so never cleaned up: its versions would sit in the
   * account for good, counting against the quota, referenced by nothing.
   */
  async function drop(uid, file) {
    await sweep(uid, file, null).catch(() => {});
    await bucket().remove([manifestPath(uid, file)]).catch(() => {});
    store.drop(mark(uid, file));
  }

  /**
   * Upload one object, reporting bytes as they go.
   *
   * XMLHttpRequest rather than the supabase-js client because `fetch` cannot
   * report upload progress at all: it resolves once, at the end. Streaming
   * request bodies would do it but need HTTP/2 and are not supported widely
   * enough to rely on. This is the same REST endpoint the client library
   * posts to, so a failure here falls back to it rather than failing the save.
   */
  function sendWithProgress(path, blob, token, contentType, onBytes) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open("POST", `${String(cfg.url).replace(/\/+$/, "")}/storage/v1/object/${BUCKET}/${path}`);
      request.setRequestHeader("apikey", cfg.anonKey);
      request.setRequestHeader("Authorization", `Bearer ${token}`);
      request.setRequestHeader("Content-Type", contentType);
      request.setRequestHeader("x-upsert", "true");
      request.upload.onprogress = (event) => {
        if (event.lengthComputable && onBytes) onBytes(event.loaded);
      };
      request.onload = () => (request.status >= 200 && request.status < 300)
        ? resolve()
        : reject(new Error(`storage answered ${request.status}`));
      request.onerror = () => reject(new Error("the network dropped the upload"));
      request.send(blob);
    });
  }

  const manifestPath = (uid, file) => `${uid}/${file}/current.json`;
  const chunkPath = (uid, file, version, i) =>
    `${uid}/${file}/${version}/${String(i).padStart(3, "0")}`;

  /** The version this browser last saw, so "has it changed?" costs no transfer. */
  const mark = (uid, file) => `villain.version.${uid}.${file}`;
  const knownVersion = (uid, file) => store.get(mark(uid, file));
  const forget = (uid) => {
    for (const file of ["db", "hero"]) store.drop(mark(uid, file));
  };

  /**
   * Strip the token Supabase leaves in the address bar.
   *
   * detectSessionInUrl has already consumed it by the time this runs; what is
   * left is a copyable URL with a live token in it.
   */
  function tidyUrl() {
    if (/access_token|error_description/.test(location.hash || "")) {
      history.replaceState({}, "", location.pathname + location.search);
    }
  }

  async function me() {
    if (!client) return null;
    const { data } = await client.auth.getSession();
    const user = data && data.session && data.session.user;
    return user ? { sub: user.id, name: user.email } : null;
  }

  async function sendLink(email) {
    const { error } = await client.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: location.origin + location.pathname },
    });
    if (error) throw new Error(error.message);
  }

  /**
   * Drop the session in this browser. Local, not global: the next page load
   * reads storage, and a reload that raced the server round-trip used to put
   * the session straight back. Revoking other devices is not what Sign out
   * on this page is for.
   */
  async function signOut() {
    if (!client) return;
    const { error } = await client.auth.signOut({ scope: "local" });
    if (error) {
      const again = await client.auth.signOut();
      if (again.error) throw new Error(again.error.message);
    }
  }

  /** `{ absent, version }` -- absent is a first sign-in, not a failure. */
  async function head(uid, file) {
    const { data, error } = await bucket().download(manifestPath(uid, file));
    if (error || !data) return { absent: true, version: null };
    try {
      const manifest = JSON.parse(await data.text());
      return { absent: false, version: manifest.version,
               chunks: manifest.chunks, size: manifest.size };
    } catch {
      return { absent: true, version: null };     // unreadable manifest: treat as nothing stored
    }
  }

  /**
   * Download every chunk of the current version and stitch it back together.
   *
   * Progress is a single fraction of the whole database, for the same reason
   * as on the way up: how many objects it happens to be stored in is not
   * something anyone waiting for it should have to think about. The manifest
   * records the total, so the fraction is known before the first byte lands.
   */
  async function get(uid, file, onProgress) {
    const current = await head(uid, file);
    if (current.absent) return { bytes: null, version: null };

    const total = Number(current.size) || 0;
    let got = 0;
    const parts = [];
    for (let i = 0; i < current.chunks; i++) {
      const { data, error } = await bucket().download(chunkPath(uid, file, current.version, i));
      if (error || !data) {
        // Flagged rather than just thrown: the copy in this browser is still
        // good, so the caller can keep working from it and write a whole one
        // back, instead of the page refusing to start over a broken remote.
        const gone = new Error(`part ${i + 1} of ${current.chunks} is missing`);
        gone.incomplete = true;
        throw gone;
      }
      const part = new Uint8Array(await data.arrayBuffer());
      parts.push(part);
      got += part.length;
      if (onProgress && total) onProgress(Math.min(1, got / total));
    }

    const joined = new Uint8Array(parts.reduce((n, part) => n + part.length, 0));
    let at = 0;
    for (const part of parts) { joined.set(part, at); at += part.length; }

    store.set(mark(uid, file), current.version);
    return { bytes: await gunzip(joined), version: current.version };
  }

  // One write at a time, per file.
  //
  // A save is several requests -- parts, then the manifest, then the sweep of
  // the old parts -- and two of them running at once corrupt each other: both
  // read the same previous version, both write a manifest, and whichever
  // manifest lands second may name a version whose parts the other one has
  // already deleted. The result reads back as "part 1 of N is missing" on the
  // next load, which is the worst kind of failure: silent until it is fatal.
  const writing = new Map();

  //: Set when a write replaced a stored copy this browser had not seen, so the
  //: interface can mention it once rather than raising an error nobody can act
  //: on.
  let overwrote = null;

  function put(uid, file, bytes, onProgress) {
    const key = `${uid}/${file}`;
    const queued = (writing.get(key) || Promise.resolve())
      .catch(() => {})                   // a failed save must not block the next
      .then(() => putNow(uid, file, bytes, onProgress));
    writing.set(key, queued);
    return queued;
  }

  /**
   * Write a new version, then point the manifest at it, then delete the old.
   *
   * That order is the only thing making a multi-object write safe: until the
   * last step the account still has a complete previous version, and a failure
   * anywhere before it leaves nothing but unreferenced chunks.
   *
   * If the stored copy moved since this browser last saw it, the write still
   * goes ahead, and says so afterwards. Refusing was worse: two SQLite files
   * cannot be merged, so the only honest choices are overwrite or give up --
   * and giving up left saving permanently broken, since every later save
   * failed the same check, while the advice to reload meant discarding
   * whatever had just been imported. A second tab was enough to trigger it.
   * The cost is that with two machines genuinely in use the later save wins;
   * the earlier one is still in that machine's own browser.
   */
  async function putNow(uid, file, bytes, onProgress) {
    const before = await head(uid, file);
    const expected = knownVersion(uid, file);
    const movedElsewhere = !before.absent && expected && before.version !== expected;

    const gz = await gzip(bytes);
    const version = (crypto.randomUUID && crypto.randomUUID())
      || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const count = Math.max(1, Math.ceil(gz.length / CHUNK));

    // Whether this takes one object or six is ours to know. Progress is
    // reported as a single fraction of the whole write, so the interface can
    // show one bar filling once rather than a counter resetting per part.
    const { data: sessionData } = await client.auth.getSession();
    const token = sessionData && sessionData.session && sessionData.session.access_token;
    let sentBefore = 0;
    const report = (bytesThisPart) => {
      if (!onProgress) return;
      onProgress(Math.min(1, (sentBefore + bytesThisPart) / Math.max(1, gz.length)));
    };

    for (let i = 0; i < count; i++) {
      const slice = gz.subarray(i * CHUNK, Math.min((i + 1) * CHUNK, gz.length));
      const blob = new Blob([slice], { type: "application/gzip" });
      const path = chunkPath(uid, file, version, i);
      report(0);
      try {
        if (!token) throw new Error("no session");
        await sendWithProgress(path, blob, token, "application/gzip", report);
      } catch (viaXhr) {
        // Progress is a courtesy; storing the data is not. Fall back to the
        // client library, which cannot report but is the better-tested path.
        const { error } = await bucket().upload(path, blob,
          { upsert: true, contentType: "application/gzip" });
        if (error) throw new Error(`could not save: ${error.message}`);
      }
      sentBefore += slice.length;
      report(0);
    }

    const manifest = { version, chunks: count, size: gz.length, written: new Date().toISOString() };
    const { error } = await bucket().upload(
      manifestPath(uid, file),
      new Blob([JSON.stringify(manifest)], { type: "application/json" }),
      { upsert: true, contentType: "application/json" });
    if (error) throw new Error(`could not save: ${error.message}`);

    store.set(mark(uid, file), version);
    if (movedElsewhere) overwrote = { file, at: Date.now() };

    // Everything except the version just written is now unreferenced. Sweeping
    // by listing, rather than deleting the one version this write replaced,
    // because a failed or interrupted save leaves a whole version behind that
    // nothing will ever point at again -- and those accumulate silently until
    // they are the reason the storage quota is full. Failing to sweep wastes
    // space but loses nothing, so it must never fail a successful save.
    await sweep(uid, file, version).catch(() => {});
    return version;
  }

  return {
    enabled, client, tidyUrl, me, sendLink, signOut,
    head, get, put, drop, knownVersion, forget,
    // Reported once and cleared, so the interface can mention an overwrite
    // without it becoming a permanent banner.
    tookOver: () => { const was = overwrote; overwrote = null; return was; },
  };
})();
