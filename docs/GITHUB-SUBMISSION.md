# Submit development to GitHub

GitHub CLI authentication is already available in this cloud environment.
This helper publishes a new branch and a **draft pull request**. Existing PR CI
runs automatically. Deployment is configured separately: the existing deploy job
requires a successful main push, its prerequisite CI jobs, and
`DEPLOY_ENABLED=true`; it also uses the production GitHub environment controls.
Creating this draft PR does not trigger that production deployment condition.

Preview the exact changed/new source paths first (read-only):

```bash
cd /workspace/Bid_it
make github-preview
```

After reviewing the diff, submit with one command:

```bash
PR_TITLE='Improve invoice workflows and account isolation' \
PR_BODY_FILE=docs/plan/DEVELOPMENT-PR.md make github-pr
```

Or supply your own title, description and new branch explicitly:

```bash
python3 scripts/github_pr.py --publish \
  --title 'Describe the concrete change' \
  --body-file /path/to/pr-description.md \
  --branch development/my-change
```

The command validates source paths and the existing index, checks GitHub auth,
fetches the remote default branch, and refuses an outdated/divergent checkout.
It then runs the existing PII gate, `make check` (backend lint, core types and
backend tests), frontend lint/build and the bundle gate. There is no skip-checks
switch. PostgreSQL, browser, security and container verification also remain in
GitHub CI; successful local checks do not claim those CI results.

Only after successful checks does it create a new branch, stage the previewed
paths, commit, push without force, and create a draft PR. It refuses staged
changes, common credentials/local-data paths and changes to files/HEAD during
verification. Generated ignored artifacts do not enter the commit. Review the
actual diff as well as this filename guard; a filename check cannot classify
every possible confidential value embedded in source.

The title is used as the commit subject and PR title; the description file is
passed literally with `--body-file`. No credentials go into the script or Git
configuration. New branch names default to `development/<UTC timestamp>`.

Failures stop the operation and retain completed steps. If upstream moved, update
your branch deliberately before retrying. If commit/push/PR creation was already
attempted, inspect `git status`, `git branch --show-current` and `gh pr list`
before resuming: a timed-out request may already have created the PR. Nothing
resets the working tree, overwrites an existing branch or force-pushes. The helper
never approves or merges a PR and never alters deployment settings.

Automation validation: four tests use a temporary real Git checkout and local bare
remote to verify successful branch/commit/push plus draft-only PR arguments,
no mutation on failed checks, refusal when file contents change during checks,
and refusal of local credentials. GitHub API calls and check commands are mocked
in those tests; no actual PR or production deployment is created by the tests.
