// Supabase project for sign-in and per-account database sync.
//
// Safe to commit: the anon key is a public, row-level-security-guarded key by
// design -- it is meant to ship in the browser, and every request it makes is
// still gated by the storage policy in web/SYNC_SETUP.md. It is not a secret.
// The service_role key is the one that must never appear here.
//
// Leave both blank to run guest-only: the preloaded demo works with no backend
// at all, so the site still deploys and is fully usable while sign-in is off.
window.VILLAIN_SUPABASE = {
  url: "https://tvfccruvwgnwbhcsacft.supabase.co",
  anonKey: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InR2ZmNjcnV2d2dud2JoY3NhY2Z0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcyNDEzNzgsImV4cCI6MjEwMjgxNzM3OH0.F1as1-fOfjrvAxEbExiZBiMfd96arid0FG11zMzfG2k",
};
