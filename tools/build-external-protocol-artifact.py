#!/usr/bin/env python3
"""Render the registry-selected B-side protocol into a CI artifact."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

from external_protocol_registry import ROOT, load_registry, select_external_protocol, select_prerelease_protocol


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--version", help="Protocol document version, for example 0.2 or v0.2")
    parser.add_argument("--stage", choices=("published", "prerelease"), default="published")
    args = parser.parse_args()
    registry = load_registry()
    external = (
        select_external_protocol(registry, args.version)
        if args.stage == "published"
        else select_prerelease_protocol(registry, args.version)
    )
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_pdf = output_dir / external["pdf_artifact_name"]
    subprocess.run(
        [str(ROOT / "tools" / "render-protocol-pdf.sh"), str(ROOT / external["markdown_path"]), str(output_pdf)],
        check=True,
    )
    print(output_pdf)


if __name__ == "__main__":
    main()
