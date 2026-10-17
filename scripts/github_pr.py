#!/usr/bin/env python3
"""Preview or publish local development as a branch and draft GitHub PR.

Default is read-only. --publish runs checks before touching the index/branch.
Never merges, force-pushes, or configures a production deployment.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAFE_ROOTS = {"backend", "frontend", "docs", "scripts", ".github"}
SAFE_ROOT_FILES = {
    ".gitignore",
    ".dockerignore",
    ".gitattributes",
    ".editorconfig",
    "README.md",
    "ARCHITECTURE.md",
    "Makefile",
    "LICENSE",
    "LICENSE.md",
    "docker-compose.yml",
    "docker-compose.hostinger.yml",
    "docker-compose.images.yml",
    ".env.example",
    ".env.hostinger.example",
}
BLOCKED_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".zip",
    ".tar",
    ".gz",
}
BLOCKED_PARTS = {
    ".git",
    ".venv",
    "node_modules",
    "var",
    "test-results",
    "playwright-report",
    "__pycache__",
    ".cache",
    ".codex",
    ".agents",
}


def run(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    if not capture:
        return ""
    return result.stdout if "-z" in args else result.stdout.strip()


def changed_paths() -> list[str]:
    tracked = run(
        "git", "diff", "--name-only", "--no-renames", "-z", "HEAD", capture=True
    )
    untracked = run(
        "git", "ls-files", "--others", "--exclude-standard", "-z", capture=True
    )
    return sorted(set(filter(None, tracked.split("\0") + untracked.split("\0"))))


def validate_paths(paths: list[str]) -> None:
    for name in paths:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("Unexpected path; review changes manually.")
        if path.parts[0] not in SAFE_ROOTS and name not in SAFE_ROOT_FILES:
            raise ValueError(f"Unreviewed top-level path: {name}")
        if (
            set(path.parts) & BLOCKED_PARTS
            or path.suffix.lower() in BLOCKED_SUFFIXES
            or any(part.startswith(".env") for part in path.parts)
            and name not in SAFE_ROOT_FILES
        ):
            raise ValueError(f"Local data or credential path refused: {name}")
        full = ROOT / path
        if full.is_symlink() or not full.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError(f"Symlink needs manual review: {name}")


def fingerprint(paths: list[str]) -> tuple:
    return tuple(
        (
            name,
            hashlib.sha256((ROOT / name).read_bytes()).digest(),
            (ROOT / name).stat().st_mode,
        )
        if (ROOT / name).is_file()
        else (name, None, None)
        for name in paths
    )


def github_repo() -> str:
    remote = run("git", "remote", "get-url", "origin", capture=True)
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?", remote
    )
    if not match:
        raise ValueError(
            "origin must be a standard GitHub HTTPS or SSH repository URL."
        )
    return match.group(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="run checks, commit, push and create a draft PR",
    )
    parser.add_argument("--title", default=os.environ.get("PR_TITLE"))
    parser.add_argument(
        "--body-file", default=os.environ.get("PR_BODY_FILE"), type=Path
    )
    parser.add_argument(
        "--branch", help="new branch name; defaults to development/<UTC timestamp>"
    )
    args = parser.parse_args()
    paths = changed_paths()
    validate_paths(paths)
    repo = github_repo()
    print(f"Repository: {repo}; {len(paths)} changed/new source paths.", flush=True)
    for path in paths:
        print(f"  {path}")
    if not args.publish:
        print(
            "Preview only. Use --publish --title TITLE --body-file FILE to submit after checks."
        )
        return 0
    if (
        not args.title
        or not args.title.strip()
        or not args.body_file
        or not args.body_file.is_file()
    ):
        raise ValueError(
            "Publishing requires a title and an existing PR description file."
        )
    body_file = args.body_file.resolve()
    if not body_file.read_text().strip():
        raise ValueError("The PR description must not be empty.")
    if not paths:
        raise ValueError("No local changes to submit.")
    if run("git", "diff", "--cached", "--name-only", capture=True):
        raise ValueError(
            "Index already contains staged changes; review or unstage them first."
        )
    branch = args.branch or "development/" + datetime.now(timezone.utc).strftime(
        "%Y%m%d-%H%M%S"
    )
    if branch in {"main", "master", "work"} or branch.startswith("-"):
        raise ValueError(
            "Choose a new development branch, not the working/default branch."
        )
    run("git", "check-ref-format", "--branch", branch)
    run("gh", "auth", "status")
    base = run("gh", "api", f"repos/{repo}", "--jq", ".default_branch", capture=True)
    run("git", "fetch", "origin", base)
    if branch == base:
        raise ValueError("The default branch cannot be the submission branch.")
    # Do not hide upstream divergence by merging or resetting the user's checkout.
    run("git", "merge-base", "--is-ancestor", f"origin/{base}", "HEAD")
    head = run("git", "rev-parse", "HEAD", capture=True)
    original = fingerprint(paths)
    run("git", "diff", "--check")
    run(sys.executable, "scripts/pii_scan.py", "--tree")
    run("make", "check")
    run("npm", "--prefix", "frontend", "run", "lint")
    run("npm", "--prefix", "frontend", "run", "build")
    run("node", "frontend/scripts/check-bundle.mjs")
    if (
        paths != changed_paths()
        or original != fingerprint(paths)
        or head != run("git", "rev-parse", "HEAD", capture=True)
    ):
        raise ValueError("The checkout changed during checks; review and run again.")
    if run("git", "diff", "--cached", "--name-only", capture=True):
        raise ValueError("The index changed during checks; review and run again.")
    run("git", "switch", "-c", branch)
    run("git", "add", "--", *paths)
    run("git", "diff", "--cached", "--check")
    run("git", "commit", "-m", args.title)
    run("git", "push", "--set-upstream", "origin", branch)
    # No retry loop: a timeout may mean GitHub created it. Print recovery guidance.
    run(
        "gh",
        "pr",
        "create",
        "--repo",
        repo,
        "--base",
        base,
        "--head",
        branch,
        "--draft",
        "--title",
        args.title,
        "--body-file",
        str(body_file),
    )
    print(
        "Draft PR created. GitHub CI runs automatically; review its checks before merging."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Submission stopped: {error}", file=sys.stderr)
        print(
            "Completed steps are retained. Inspect git status and gh pr list before retrying; nothing is reset or force-pushed.",
            file=sys.stderr,
        )
        raise SystemExit(1)
