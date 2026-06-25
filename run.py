import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bunnytrail_cli.main import cli

if __name__ == "__main__":
    cli()
