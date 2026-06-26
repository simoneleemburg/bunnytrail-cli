import sys
import os
from pathlib import Path

_here = Path(os.path.abspath(__file__)).parent
sys.path.insert(0, str(_here))
sys.path.insert(0, str(_here / "vendor"))

# Evict any pre-loaded copies of vendored packages so the versions in
# vendor/ are imported instead of Pythonista's potentially outdated ones.
for _mod in list(sys.modules):
    if _mod == "yaml" or _mod.startswith("yaml."):
        del sys.modules[_mod]

from bunnytrail_cli.helpers import find_project_root
from bunnytrail_cli.repl import run_shell

if __name__ == "__main__":
    # First try the sibling alteria_world folder (works when both repos are
    # checked out side-by-side, e.g. on Pythonista/iOS).
    sibling = _here.parent / "alteria_world.git"
    if sibling.is_dir():
        try:
            project = find_project_root(start=sibling)
        except FileNotFoundError:
            project = None
    else:
        project = None

    # Fall back to walking up from cwd (normal desktop usage).
    if project is None:
        try:
            project = find_project_root()
        except FileNotFoundError:
            project = None

    # Last resort: hardcoded fallback — edit this path if auto-detection fails.
    if project is None:
        FALLBACK_PATH = ""  # e.g. "/var/mobile/.../alteria_world"
        if FALLBACK_PATH:
            project = Path(FALLBACK_PATH).expanduser().resolve()
        else:
            raise FileNotFoundError(
                "Could not find the alteria_world project.\n"
                "Set the FALLBACK_PATH variable in run.py to the full path."
            )

    run_shell(project)
