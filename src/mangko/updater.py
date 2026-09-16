"""Auto-update from upstream repo.

On restart (via start.sh) this pulls the latest code from UPSTREAM_REPO.
"""

import logging
from pathlib import Path
from subprocess import run

from mangko.config import get_settings

logging.basicConfig(
    format="[%(asctime)s] %(levelname)s - %(message)s",
    level=logging.INFO,
    datefmt="%d-%b %I:%M:%S %p",
)
log = logging.getLogger("update")

# Files that must not be wiped by git reset (session / user data)
PRESERVE = {
    ".env",
    "mangko.session",
    "mangko.session-journal",
}


def main() -> int:
    settings = get_settings()

    if not settings.upstream_repo:
        log.info("UPSTREAM_REPO empty – skip update")
        return 0

    log.info("Updating from %s (%s)", settings.upstream_repo, settings.upstream_branch)

    # Drop local .git so reset is always against the upstream remote
    if Path(".git").exists():
        run(["rm", "-rf", ".git"], check=False)

    cmd = (
        f"git init -q "
        f"&& git config --global user.email hstream@local "
        f"&& git config --global user.name mangko "
        f"&& git add . "
        f"&& git commit -sm update -q || true "
        f"&& git remote add origin {settings.upstream_repo} "
        f"&& git fetch origin -q "
        f"&& git reset --hard origin/{settings.upstream_branch} -q"
    )
    result = run(cmd, shell=True, check=False)
    if result.returncode == 0:
        log.info("Successfully updated with latest commit from UPSTREAM_REPO")
    else:
        log.error(
            "Update failed (check UPSTREAM_REPO / network). Continuing with current files."
        )
        return 0  # do not block bot start

    # Optional: refresh deps if pyproject.toml changed
    if Path("pyproject.toml").is_file():
        run(["uv", "sync"], check=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
