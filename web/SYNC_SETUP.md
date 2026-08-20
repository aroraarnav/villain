# Turning on sign-in

The hosted app runs without a backend of its own. Sign-in is optional: leave
`web/config.js` blank and every visitor gets the demo. Fill it in and visitors
are asked which way in they want — an emailed link, or the read-only demo — and
a signed-in visitor gets their own private database that follows them between
machines.

Hands are still parsed in the browser. What crosses the wire is the finished
SQLite file, gzipped and cut into parts, readable only by the account that
wrote it.

**No card is required at any point.** Supabase's free tier covers all of this:
50,000 monthly active users, 1 GB of file storage, 5 GB of egress a month.

The one limit that shapes the design is **50 MB per file**. A 98 MB database
gzips to about 60, so the page splits it into 40 MB parts and stitches them back
together on the way in. That is automatic and size-independent — a small
database is one part, a large one is as many as it needs.

Two things to know before you start:

- **A free project pauses after a week with no traffic.** Sign-in stops working
  until you resume it from the dashboard. Step 5 keeps it awake for free.
- **Storage is 1 GB for the whole project**, and a save briefly holds both the
  old version and the new. So one account's database should stay under about
  400 MB — far above anything a home game will produce.

## 1. Create the project

1. Open [supabase.com/dashboard](https://supabase.com/dashboard) and sign in
   (GitHub is fine).
2. **New project.** A name, a database password you will not need day-to-day,
   a region close to you. Wait for it to finish provisioning.

## 2. Turn on emailed links

1. **Authentication → Providers → Email.** Enabled, with **Confirm email** on.
2. **Authentication → URL configuration.**
   - **Site URL:** `https://aroraarnav.github.io/villain`
   - **Redirect URLs,** one per line:
     ```
     https://aroraarnav.github.io/villain/
     http://127.0.0.1:8000/
     http://localhost:8000/
     ```

On the free tier the mail comes from Supabase's shared sender and is rate
limited to a handful an hour, which is fine for a few people. Check spam the
first time. Custom SMTP is a later upgrade, not a requirement.

The page asks for the link with the **implicit** flow rather than PKCE, on
purpose: PKCE keeps a code verifier in the browser that *requested* the link, so
opening the mail on a phone fails — which is exactly what an account is meant to
make possible.

## 3. Create the private bucket

**SQL Editor → New query**, paste, run:

```sql
insert into storage.buckets (id, name, public)
values ('dbs', 'dbs', false)
on conflict (id) do nothing;

-- Dropped first so this whole block can be run again safely; `create policy`
-- is an error, not a no-op, when the policy is already there.
drop policy if exists "own files only" on storage.objects;

create policy "own files only"
on storage.objects for all
to authenticated
using (
  bucket_id = 'dbs'
  and (storage.foldername(name))[1] = auth.uid()::text
)
with check (
  bucket_id = 'dbs'
  and (storage.foldername(name))[1] = auth.uid()::text
);
```

Every object an account owns lives under a folder named for its user id:

```
<user-id>/db/current.json                 the manifest: which version is live
<user-id>/db/<version>/000, 001, …        the parts of that version
<user-id>/hero/…                          the same, for the hero cache
```

That policy is the whole privacy model: authenticated, and only the folder
named for you. The page never chooses that folder itself — it comes from the
session — so one account cannot name another's path.

## 4. Put the keys in the page

**Project Settings → API:**

- **Project URL** → `url` in `web/config.js`
- **anon / public** key → `anonKey`

The anon key is designed to ship in the browser. It is not a secret; the policy
above is what stops it reading anyone else's files. Never put the
**service_role** key in this repo.

Redeploy (push to `main`, or `python web/build.py` locally). The sign-in page
appears as soon as both fields are non-empty.

## 5. Keep the project awake

A free project pauses after seven days of no traffic, and a paused project means
nobody can sign in. `.github/workflows/keepalive.yml` prevents it with one
request a week. It needs no configuration — the URL and anon key are already
public, so they sit in the workflow rather than in Actions secrets, which would
imply a confidentiality neither has.

It pings **auth** and **storage**. Auth on its own may not count as *database*
activity; listing the bucket makes Postgres evaluate the row-level policy, which
is a real query. PostgREST is deliberately not pinged — the anon role has no
grants, so `/rest/v1/` answers 401 and would prove nothing. The job fails if
either call stops returning 200, so a broken ping is visible instead of quietly
succeeding every week while the project sleeps.

One caveat: **GitHub disables scheduled workflows in a repository with no
activity for 60 days.** If this repo goes quiet that long the ping stops. Re-run
it from the Actions tab and resume the project from the Supabase dashboard.

## What a guest can do

With sync configured, the demo is readable by anyone and writable by nobody. A
guest can open every screen, read every profile and play the simulator;
importing, merging, renaming, noting and resetting all need an account. That is
not a paywall — those are the operations whose results are worth keeping, and a
guest has nowhere to keep them.

The lock lives in the page, not in `villain/webapp/server.py`, because the same
module backs `villain ui` on a laptop, which has no accounts and stays fully
writable.

## What it costs to run

| | free allowance | what a heavy user spends |
| --- | --- | --- |
| Monthly active users | 50,000 | one, probably |
| File storage | 1 GB | ~60 MB for a 71,000-hand database |
| Egress | 5 GB / month | only on a new device, or after another one wrote |
| Emailed links | a few an hour | one per sign-in, then a month of not needing it |

The database is downloaded only when the copy on the server is newer than the
one already in this browser. Day-to-day use on your own laptop reads one small
manifest and transfers nothing else.
