#!/usr/bin/env python3
"""Convert the single internal prerelease Markdown into a new release Markdown."""

from __future__ import annotations

import argparse
from pathlib import Path

from external_protocol_registry import ROOT, external_protocol_catalog, load_registry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True, help="Must match the active prerelease version")
    parser.add_argument("--output", required=True, type=Path, help="New external Markdown path")
    args = parser.parse_args()
    registry = load_registry()
    prerelease = registry["documents"]["internal_prerelease"]
    catalog = external_protocol_catalog(registry)
    version = args.version.removeprefix("v")
    if version != prerelease["version"]:
        raise SystemExit("Only the active prerelease version can be promoted.")

    source_path = ROOT / prerelease["markdown_path"]
    output_path = args.output.resolve()
    if output_path.exists():
        raise SystemExit(f"Refusing to overwrite existing release document: {output_path}")
    text = source_path.read_text(encoding="utf-8")
    text = text.replace(f"# {catalog['title']} 预发布 v{version}", f"# {catalog['title']} v{version}", 1)
    text = text.replace(f"版本：v{version}（预发布）<br>", f"版本：v{version}<br>", 1)
    text = text.replace("状态：**预发布；仅供内部评审，尚未对 B 端发布**", "状态：**接口基线；联调前由双方确认有线直连地址、端口与启用流**", 1)
    text = text.replace(f"## 预发布变更（v{version}）", f"## v{version} 变更", 1)
    text = text.replace("<!-- protocol-prerelease-change:start -->\n", "", 1)
    text = text.replace("<!-- protocol-prerelease-change:end -->\n", "", 1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
