#!/usr/bin/env python3
"""Create the single internal prerelease protocol from the latest release."""

from __future__ import annotations

import argparse
from pathlib import Path
import re

from external_protocol_registry import ROOT, external_protocol_catalog, load_registry, select_external_protocol


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overwrite", action="store_true", help="Replace the existing single internal prerelease document")
    args = parser.parse_args()
    registry = load_registry()
    lifecycle = registry["protocol_lifecycle"]
    prerelease = registry["documents"]["internal_prerelease"]
    catalog = external_protocol_catalog(registry)
    source = select_external_protocol(registry, prerelease["based_on_released_version"])
    if lifecycle["released_version"] != catalog["current_version"]:
        raise SystemExit("Released protocol version must match the external protocol catalog.")
    if prerelease["version"] != lifecycle["prerelease_version"]:
        raise SystemExit("Prerelease document version must match the protocol lifecycle.")

    source_path = ROOT / source["markdown_path"]
    output_path = ROOT / prerelease["markdown_path"]
    if output_path.exists() and not args.overwrite:
        raise SystemExit(f"Prerelease document already exists: {output_path}")

    source_text = source_path.read_text(encoding="utf-8")
    released_version = source["version"]
    prerelease_version = prerelease["version"]
    title = catalog["title"]
    output_text = source_text.replace(
        f"# {title} v{released_version}", f"# {title} 预发布 v{prerelease_version}", 1
    )
    output_text = output_text.replace(
        f"版本：v{released_version}<br>", f"版本：v{prerelease_version}（预发布）<br>", 1
    )
    output_text = re.sub(
        r"状态：\*\*.*?\*\*",
        "状态：**预发布；仅供内部评审，尚未对 B 端发布**",
        output_text,
        count=1,
    )
    output_text = output_text.replace(f"v{released_version}", f"v{prerelease_version}")
    prerelease_changes = f"""
<!-- protocol-prerelease-change:start -->
## 预发布变更（v{prerelease_version}）

本节优先于本文其他与录播 `getLatest` 有冲突的描述。

- 录播结果依次为 `1,2,3,4,5,6,7,8`，当前 WebSocket 连接已发送 `4` 后，`getLatest` 必须返回 `4`，不得预读 `5` 至 `8`。
- 当前连接尚未回放出算法结果时，`getLatest` 返回 `code=200`、`valid=false` 和空 `payload`，且不会暗中启动持续录播。
- 未连接头环时，只有 `subscribe` 启动该连接的录播进度；之后的 `getLatest` 读取该进度。
<!-- protocol-prerelease-change:end -->

"""
    insert_at = output_text.find("## 1. 使用范围")
    if insert_at < 0:
        raise SystemExit("Expected protocol scope heading is missing.")
    output_path.write_text(output_text[:insert_at] + prerelease_changes + output_text[insert_at:], encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
