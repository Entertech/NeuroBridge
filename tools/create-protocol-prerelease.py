#!/usr/bin/env python3
"""Create an internal prerelease from the corresponding current formal draft."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.promote_document import create_draft


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document", default="northbound")
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(create_draft(args.document, args.version.removeprefix("v")))


if __name__ == "__main__":
    main()
