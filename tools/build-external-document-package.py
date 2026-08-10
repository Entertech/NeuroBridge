#!/usr/bin/env python3
"""Render all current external Markdown documents and package their PDFs for CI."""

from __future__ import annotations

import argparse
from pathlib import Path

from external_document_package import build_package


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory receiving the ZIP and release manifest")
    parser.add_argument("--pdf-dir", required=True, type=Path, help="Temporary directory for generated PDFs")
    parser.add_argument("--mode", choices=("candidate", "publish"), default="candidate")
    args = parser.parse_args()
    package_path = build_package(args.output_dir.resolve(), args.pdf_dir.resolve(), args.mode)
    print(package_path)


if __name__ == "__main__":
    main()
