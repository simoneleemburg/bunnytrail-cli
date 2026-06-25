import sys
import os

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
sys.path.insert(0, os.path.join(_here, "vendor"))

from bunnytrail_cli.helpers import find_project_root
from bunnytrail_cli.repl import run_shell

if __name__ == "__main__":
    run_shell(find_project_root())
