"""Allow running `python -m llmsvet`."""

import sys
from llmsvet.cli import main

if __name__ == "__main__":
    sys.exit(main())
