#!/usr/bin/env python3
"""Package static capture and B-side pages without local logs or recordings."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import zipfile

from external_document_package import collect_web_client_files

ROOT = Path(__file__).resolve().parents[1]
CAPTURE_FILES = ('index.html', 'app.js', 'styles.css', 'README.md')


def build(output: Path) -> Path:
    entries = [(f'capture/{name}', ROOT / 'web/capture' / name) for name in CAPTURE_FILES]
    entries.extend((f'b-client-test/{path.name}', path) for path in collect_web_client_files())
    payloads = {}
    for name, path in entries:
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to((ROOT / 'web').resolve()):
            raise ValueError(f'Missing or unsafe static console input: {name}')
        contents = path.read_bytes()
        if path.suffix == '.bat':
            contents = contents.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        payloads[name] = contents
    manifest = {'captureEntry': 'capture/index.html', 'bSideEntry': 'b-client-test/index.html', 'files': [{'path': name, 'sha256': sha256(data).hexdigest(), 'bytes': len(data)} for name, data in payloads.items()]}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, contents in payloads.items():
            bundle.writestr(name, contents)
        bundle.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    with zipfile.ZipFile(output) as bundle:
        if bundle.testzip() is not None or set(bundle.namelist()) != set(payloads) | {'manifest.json'}:
            raise RuntimeError('Static console archive validation failed')
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'output/neurobridge-static-console.zip')
    print(build(parser.parse_args().output).resolve())


if __name__ == '__main__':
    main()
