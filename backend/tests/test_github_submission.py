"""Exercise real git branch/index/push behavior without publishing to GitHub."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/github_pr.py"


@pytest.fixture
def submission(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("github_submission", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root, remote = tmp_path / "checkout", tmp_path / "remote.git"
    root.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "--initial-branch=main")
    git("config", "user.name", "Synthetic Submission")
    git("config", "user.email", "submission@example.com")
    git("config", "commit.gpgsign", "false")
    (root / "README.md").write_text("Initial\n")
    git("add", "README.md")
    git("commit", "-m", "Initial")
    git("init", "--bare", str(remote))
    git("remote", "add", "origin", str(remote))
    git("push", "origin", "main")
    (root / "README.md").write_text("Reviewed change\n")
    body = tmp_path / "body.md"
    body.write_text("A concrete synthetic change for review.\n")
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--publish",
            "--title",
            "Synthetic change",
            "--body-file",
            str(body),
            "--branch",
            "development/synthetic",
        ],
    )
    events = []
    real_run = module.run

    def run(*args, **kwargs):
        events.append(args)
        if args == ("git", "remote", "get-url", "origin"):
            return "https://github.com/example/synthetic.git"
        if args[0] == "gh":
            return "main" if args[1] == "api" else ""
        if args[0] in {"make", "npm", "node", sys.executable}:
            return ""
        return real_run(*args, **kwargs)

    monkeypatch.setattr(module, "run", run)
    return module, root, git, events, run


def test_submission_creates_new_remote_branch_and_draft_only(submission):
    module, root, git, events, _ = submission
    main = git("rev-parse", "main")
    assert module.main() == 0
    assert git("rev-parse", "main") == main
    assert git("branch", "--show-current") == "development/synthetic"
    assert git("show", "origin/development/synthetic:README.md") == "Reviewed change"
    assert not git("status", "--porcelain")
    pr = next(event for event in events if event[:3] == ("gh", "pr", "create"))
    assert "--draft" in pr and pr[pr.index("--base") + 1] == "main"


def test_failed_checks_leave_original_branch_and_index(submission, monkeypatch):
    module, _, git, events, run = submission

    def fail(*args, **kwargs):
        if args[0] == "make":
            raise subprocess.CalledProcessError(1, args)
        return run(*args, **kwargs)

    monkeypatch.setattr(module, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        module.main()
    assert git("branch", "--show-current") == "main"
    assert not git("diff", "--cached", "--name-only")
    assert not any(event[:2] == ("git", "push") for event in events)


def test_changes_during_checks_refuse_publication(submission, monkeypatch):
    module, root, git, events, run = submission

    def modify(*args, **kwargs):
        if args[0] == "make":
            (root / "README.md").write_text("Changed during verification\n")
        return run(*args, **kwargs)

    monkeypatch.setattr(module, "run", modify)
    with pytest.raises(ValueError, match="checkout changed"):
        module.main()
    assert git("branch", "--show-current") == "main"
    assert not any(event[:2] == ("git", "push") for event in events)


def test_local_credentials_refused_before_git_mutation(submission):
    module, root, git, events, _ = submission
    (root / ".env").write_text("SYNTHETIC_ONLY=true\n")
    with pytest.raises(ValueError):
        module.main()
    assert git("branch", "--show-current") == "main"
    assert not any(event[:2] == ("git", "switch") for event in events)
