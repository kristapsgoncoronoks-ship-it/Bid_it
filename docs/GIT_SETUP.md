# Putting this project under git

Run these once, in the project folder, to start tracking changes.

## 1. Initialise
```
git init
git add .
git commit -m "Initial commit: Fleet Fuel & VAT Refund System"
```
`.gitignore` already excludes secrets, certificates, security.db, and runtime
files — verify nothing sensitive is staged before the first commit:
```
git status            # review the list
git ls-files | grep -E "secret|cert|key|security.db"   # should print NOTHING
```

## 2. (Optional) Push to a private remote
Use a PRIVATE repository — this contains business data and master records.
GitHub example:
```
git remote add origin https://github.com/<you>/fleet-fuel.git
git branch -M main
git push -u origin main
```
EU-hosted alternatives if you prefer to keep code in the EU: GitLab.com (or a
self-hosted GitLab), Codeberg (Germany), or your company's internal git server.

## 3. Day-to-day
```
git add -A
git commit -m "describe what changed"     # after each working change
git log --oneline                          # history
git revert <hash>                          # undo a specific change safely
```

## 4. Working with Claude Code
With git in place, Claude Code (claude.ai/code or the CLI) can open this folder,
read CLAUDE.md for context, make changes on a branch, run the app, and commit.
Recommended flow: a branch per task (`git checkout -b add-wholesale-feed`), let
Claude Code work there, review the diff, merge when happy.

## Databases in git — your choice
- customers.db / suppliers.db / fuel_history.db ARE tracked by default so a clone
  is immediately usable with the May 2026 data.
- security.db is NEVER tracked (password hashes).
- If you'd rather keep ALL business data out of git, add the three DBs to
  .gitignore and instead commit a seed script. For a single-maintainer project,
  tracking them is simpler; for a shared public-ish repo, exclude them.
