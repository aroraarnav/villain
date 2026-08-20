/**
 * Villain's Python, off the main thread.
 *
 * Everything the tool actually does -- parsing hand histories, rebuilding
 * profiles, fitting the hero model -- is one long synchronous call into
 * CPython compiled to WebAssembly. Run on the page's own thread, as it was,
 * the tab cannot repaint or answer a click for the duration: Chrome puts up
 * "page unresponsive", spinners freeze mid-turn, and a three-minute model fit
 * is indistinguishable from a crash. None of that is fixed by making the work
 * faster; it is fixed by doing it somewhere else.
 *
 * So the runtime lives here and the page talks to it by message. The page
 * keeps the two things a worker cannot have: the DOM, and the Supabase
 * session, which lives in localStorage and is unreachable from here. Database
 * bytes therefore travel across this boundary rather than being fetched here.
 *
 * Every message carries an `id` and gets exactly one reply with the same `id`.
 */

let pyodide = null;
let bridge = null;
let manifest = null;

const DIR = "/persist";
const DB = `${DIR}/villain.db`;
const HERO = `${DB}.hero-cache.json`;

const say = (id, ok, payload) => self.postMessage({ id, ok, ...payload });
const step = (text, pct) => self.postMessage({ type: "step", text, pct });

const exists = (path) => pyodide.FS.analyzePath(path).exists;

const toDisk = (fromDisk) => new Promise((done, fail) =>
  pyodide.FS.syncfs(fromDisk, (err) => (err ? fail(err) : done())));

/** Create an empty database, the way a first sign-in needs one. */
function emptyDb() {
  pyodide.runPython(`
from villain.db import Store
with Store("${DB}"):
    pass
`);
}

async function boot(base) {
  step("Downloading the Python runtime…", 8);
  importScripts("https://cdn.jsdelivr.net/pyodide/v0.27.2/full/pyodide.js");
  pyodide = await self.loadPyodide();

  step("Loading numpy, scipy and scikit-learn…", 30);
  await pyodide.loadPackage(["numpy", "scipy", "scikit-learn", "sqlite3", "micropip"]);

  step("Installing Villain…", 55);
  manifest = await (await fetch(new URL("manifest.json", base))).json();
  // Absolute, not the bare filename out of the manifest: micropip resolves a
  // relative name against its own notion of the current directory and lands on
  // file:///, which it then refuses as a non-remote location.
  await pyodide.pyimport("micropip").install(new URL(manifest.wheel, base).href, { deps: false });

  // The working copy lives in this browser either way, signed in or not.
  // IndexedDB is reachable from a worker; localStorage is not, which is why
  // the session stays on the page.
  pyodide.FS.mkdir(DIR);
  pyodide.FS.mount(pyodide.FS.filesystems.IDBFS, {}, DIR);
  await toDisk(true);

  bridge = pyodide.pyimport("villain.webapp.browser");
  bridge.set_db(DB);
  return { manifest };
}

/** The demo database, for a visitor who has not signed in. */
async function seedDemo(base) {
  if (exists(DB)) return { seeded: false };
  const grab = async (name) => {
    const res = await fetch(new URL(name, base));
    return res.ok ? new Uint8Array(await res.arrayBuffer()) : null;
  };
  const db = await grab(manifest.db);
  if (db) pyodide.FS.writeFile(DB, db);
  if (manifest.hero_cache) {
    const hero = await grab(manifest.hero_cache);
    if (hero) pyodide.FS.writeFile(HERO, hero);
  }
  await toDisk(false);
  return { seeded: !!db };
}

const handlers = {
  boot: ({ base }) => boot(base),
  seedDemo: ({ base }) => seedDemo(base),

  /**
   * The Hero build, which is the one request long enough to need reporting.
   *
   * Routed around dispatch_json because progress has to escape a call that
   * takes minutes, and an HTTP-shaped interface has nowhere to put it. Python
   * calls straight back into this function as it walks.
   */
  hero: () => {
    const report = (done, total, phase) =>
      self.postMessage({ type: "hero", done, total, phase });
    const proxy = bridge.build_hero(report);
    const out = proxy.toJs({ dict_converter: Object.fromEntries });
    proxy.destroy();
    return out;
  },

  /** One API request, answered exactly as the local server would answer it. */
  api: ({ method, path, body }) => {
    const proxy = bridge.dispatch_json(method, path, body || "");
    const out = proxy.toJs({ dict_converter: Object.fromEntries });
    proxy.destroy();
    return out;
  },

  read: ({ path }) => {
    if (!exists(path)) return { bytes: null };
    // Transferred, not copied: a 98 MB database has no business being cloned
    // on its way to a thread that is only going to gzip it.
    const bytes = pyodide.FS.readFile(path);
    return { bytes, transfer: [bytes.buffer] };
  },

  write: ({ path, bytes }) => {
    pyodide.FS.writeFile(path, new Uint8Array(bytes));
    return { ok: true };
  },

  remove: ({ path }) => {
    if (exists(path)) pyodide.FS.unlink(path);
    return { ok: true };
  },

  exists: ({ path }) => ({ exists: exists(path) }),
  flush: async ({ fromDisk }) => { await toDisk(!!fromDisk); return { ok: true }; },
  fresh: () => { emptyDb(); return { ok: true }; },
  paths: () => ({ dir: DIR, db: DB, hero: HERO }),
};

self.onmessage = async (event) => {
  const { id, kind, ...args } = event.data || {};
  const handler = handlers[kind];
  if (!handler) return say(id, false, { error: `no such worker call: ${kind}` });
  try {
    const result = await handler(args);
    const transfer = result && result.transfer;
    if (transfer) delete result.transfer;
    self.postMessage({ id, ok: true, ...result }, transfer || []);
  } catch (err) {
    say(id, false, { error: (err && err.message) || String(err) });
  }
};
