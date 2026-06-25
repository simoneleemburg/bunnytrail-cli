import sys
import os
from pathlib import Path

_here = Path(os.path.abspath(__file__)).parent
sys.path.insert(0, str(_here))
sys.path.insert(0, str(_here / "vendor"))

from bunnytrail_cli.helpers import find_project_root
from bunnytrail_cli.repl import run_shell

if __name__ == "__main__":
    # First try the sibling alteria_world folder (works when both repos are
    # checked out side-by-side, e.g. on Pythonista/iOS).
    sibling = _here.parent / "alteria_world"
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

    # Last resort: ask the user.
    if project is None:
        raw = input("Could not find alteria_world. Enter the full path to the project: ").strip()
        project = Path(raw).expanduser().resolve()

    run_shell(project)
