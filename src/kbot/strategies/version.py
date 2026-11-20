"""``strategy_version`` = commit + hash of the parameters (``02`` §12.3.3).

* **Commit:** the 40 hexadecimal characters of the commit of the code that runs. If a file
  that git follows has uncommitted changes, there is no version, and nothing is recorded as
  of a version. Files git does not follow are not part of any commit and do not count.
* **Parameters:** a JSON object with the family, its timeframes, the regime and every
  parameter of the ficha. Periods and counts are integers; thresholds and multiples are
  ``Decimal``, in canonical text (§5.7).
* **Hash:** SHA-256, in hexadecimal, of the canonical JSON of RFC 8785 (``kbot.core.jcs``),
  as for ``config.risk_limits``.
* **Text:** ``<commit>+<hash>``.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

from kbot.core.jcs import canonicalize

COMMIT = re.compile(r"[0-9a-f]{40}")


class NoVersion(RuntimeError):
    """The code that runs has no version: uncommitted changes, or no commit to name."""


def params_hash(params: Mapping[str, object]) -> str:
    return hashlib.sha256(canonicalize(params)).hexdigest()


def strategy_version(commit: str, params: Mapping[str, object]) -> str:
    if not COMMIT.fullmatch(commit):
        raise ValueError(f"a commit is 40 lowercase hexadecimal characters, got {commit!r}")
    return f"{commit}+{params_hash(params)}"


def repository_commit(repository: Path) -> str:
    """The commit checked out in ``repository``. Raises ``NoVersion`` if a followed file
    has uncommitted changes."""
    git = shutil.which("git")
    if git is None:
        raise NoVersion("git is not available, so the commit cannot be known")
    commit = _git(git, repository, "rev-parse", "HEAD")
    if _git(git, repository, "status", "--porcelain", "--untracked-files=no"):
        raise NoVersion("the working tree has uncommitted changes: there is no version")
    if not COMMIT.fullmatch(commit):
        raise NoVersion(f"unexpected commit {commit!r}")
    return commit


def _git(git: str, repository: Path, *arguments: str) -> str:
    done = subprocess.run(  # noqa: S603 - git found by shutil.which, fixed arguments
        [git, "-C", str(repository), *arguments], capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise NoVersion(f"git {' '.join(arguments)} failed: {done.stderr.strip()}")
    return done.stdout.strip()
