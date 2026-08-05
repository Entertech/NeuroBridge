#!/usr/bin/env python3
"""Render the registry-selected B-side protocol into a CI artifact."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    registry = tomllib.loads((ROOT / "neurobridge" / "version_registry.toml").read_text(encoding="utf-8"))
    external = registry["documents"]["external_northbound"]
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
