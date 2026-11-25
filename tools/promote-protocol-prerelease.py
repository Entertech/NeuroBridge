#!/usr/bin/env python3
"""Compatibility entry point for promoting the northbound prerelease."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.promote_document import main


if __name__ == "__main__":
    main()
