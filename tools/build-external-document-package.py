#!/usr/bin/env python3
"""Render all current external Markdown documents and package their PDFs for CI."""

from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from .external_document_package import build_package
else:
    from external_document_package import build_package
import json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory receiving the ZIP and release manifest")
    parser.add_argument("--pdf-dir", required=True, type=Path, help="Temporary directory for generated PDFs")
    parser.add_argument("--mode", choices=("candidate", "publish"), default="candidate")
    parser.add_argument("--preflight", type=Path)
    args = parser.parse_args()
    preflight = json.loads(args.preflight.read_text(encoding="utf-8")) if args.preflight else None
    package_path = build_package(args.output_dir.resolve(), args.pdf_dir.resolve(), args.mode, preflight=preflight)
    print(package_path)


if __name__ == "__main__":
    main()
