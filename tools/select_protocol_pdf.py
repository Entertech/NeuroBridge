"""Check document history and select the official protocol PDF for CI."""

import argparse
from pathlib import Path

from tools.external_protocol_registry import select_external_protocol
from tools.release_scope import registry_at
from tools.release_version_policy import check_document_versions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="Pull request target commit")
    parser.add_argument("--version", help="Registered version for a manual build")
    parser.add_argument("--output", type=Path, required=True, help="GitHub Actions step output file")
    args = parser.parse_args()

    registry = registry_at("HEAD")
    version = args.version
    try:
        if args.base:
            previous = registry_at(args.base)
            check_document_versions(args.base, "HEAD", previous, registry)
            version = registry["documents"]["external_northbound"]["current_version"]
            if version == previous["documents"]["external_northbound"]["current_version"]:
                return
        document = select_external_protocol(registry, version)
    except ValueError as error:
        parser.error(str(error))

    with args.output.open("a", encoding="utf-8", newline="\n") as output:
        output.write(f"version={document['version']}\n")
        output.write(f"filename={document['pdf_artifact_name']}\n")


if __name__ == "__main__":
    main()
