"""Back up the campaigns directory to its git remote.

The campaigns directory ($DM20_STORAGE_DIR/campaigns, one folder per campaign) is a
clone of a private repository. A backup is: stage everything, commit if anything
changed, push.

This module uses only the standard library and can be run as a plain script, without
importing the dm20 package (the instance's shutdown hook does this):

    python3 backup.py /path/to/campaigns "commit message"
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Never backed up: Party Mode image cache (rebuilt from the art source) and QR codes
# (they embed player session tokens).
IGNORE_LINES = ["*/party/images/", "*/party/*.png", ".DS_Store"]
GIT_TIMEOUT_S = 90
IDENTITY = ["-c", "user.name=dm20 server", "-c", "user.email=dm20@localhost"]


@dataclass
class BackupResult:
    ok: bool
    message: str
    commit: str | None = None
    committed: bool = False
    pushed: bool = False


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
    )


def ensure_gitignore(repo: Path) -> None:
    path = repo / ".gitignore"
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    missing = [line for line in IGNORE_LINES if line not in existing]
    if missing:
        path.write_text("\n".join(existing + missing) + "\n", encoding="utf-8")


def backup(repo: Path, message: str = "") -> BackupResult:
    repo = Path(repo)
    if not (repo / ".git").exists():
        return BackupResult(False, f"Not backed up: {repo} is not a git repository (no campaign repo configured).")
    try:
        ensure_gitignore(repo)
        add = _git(repo, "add", "-A")
        if add.returncode != 0:
            return BackupResult(False, f"Not backed up: git add failed: {add.stderr.strip()}")

        committed = False
        if _git(repo, "diff", "--cached", "--quiet").returncode != 0:
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            msg = (message.strip() or "Campaign backup") + f" ({stamp})"
            commit = _git(repo, *IDENTITY, "commit", "-q", "-m", msg)
            if commit.returncode != 0:
                return BackupResult(False, f"Not backed up: git commit failed: {commit.stderr.strip()}")
            committed = True
        head = _git(repo, "rev-parse", "--short", "HEAD")
        sha = head.stdout.strip() if head.returncode == 0 else None

        if not _git(repo, "remote").stdout.strip():
            return BackupResult(
                False, "Saved locally only: the campaign repo has no remote to push to.", sha, committed
            )
        push = _git(repo, "push", "-q", "origin", "HEAD")
        if push.returncode != 0:
            return BackupResult(
                False,
                f"Committed locally ({sha}) but push failed: {push.stderr.strip()[-400:]}",
                sha,
                committed,
            )
        what = "New commit pushed" if committed else "No changes since the last backup; remote is up to date"
        return BackupResult(True, f"{what} ({sha}).", sha, committed, True)
    except subprocess.TimeoutExpired:
        return BackupResult(False, "Not backed up: git timed out.")
    except FileNotFoundError:
        return BackupResult(False, "Not backed up: git is not installed.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: backup.py <campaigns-dir> [message]", file=sys.stderr)
        sys.exit(2)
    result = backup(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "")
    print(result.message)
    sys.exit(0 if result.ok else 1)
