#!/usr/bin/env python3
"""Build and package one release of the monthly reference index (the step the public
repository's workflow runs; also exported there as builder/build_release.py).

  python -I build_release.py --dblp-dump dblp.xml.gz --work-dir work --out-dir release --notes notes.md

Builds the index from a DBLP dump whose checksum the caller has already verified, checks it
(record count, normalization fingerprint, well-known works found), writes
<asset>.sqlite.xz and manifest.json into --out-dir and the release notes into --notes.
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _path in (_HERE, _HERE.parent / "src"):  # exported layout, then the product repository
    if (_path / "el_offline_admission").is_dir():
        sys.path.insert(0, str(_path))
        break

from el_offline_admission.reference_index import build_dblp_index  # noqa: E402
from el_offline_admission.reference_index_release import MIN_RECORDS, PROBE_TITLES, package_index  # noqa: E402


def release_notes(manifest: dict) -> str:
    asset = manifest["asset"]
    return "\n".join([
        f"DBLP reference index for Evidence Locker, built {manifest['built_at_utc'][:10]}.",
        "",
        f"- Publications: {manifest['records']:,}",
        f"- DBLP dump SHA-256: `{manifest['dump_sha256']}`",
        f"- Download: `{asset['name']}` ({asset['size']:,} bytes, SHA-256 `{asset['sha256']}`)",
        f"- Index schema {manifest['index_schema_version']}, normalization fingerprint "
        f"`{manifest['normalization_fingerprint']}`",
        "",
        "Data: dblp computer science bibliography, https://dblp.org, released under CC0 1.0.",
        "The Evidence Locker app downloads and checks this file itself; see manifest.json.",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dblp-dump", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--notes", required=True)
    parser.add_argument("--release-tag", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    # Tests only: a tiny fixture dump has neither 5 million records nor the probe works.
    parser.add_argument("--min-records", type=int, default=MIN_RECORDS, help=argparse.SUPPRESS)
    parser.add_argument("--no-probes", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    work, out = Path(args.work_dir), Path(args.out_dir)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    def built(count: int) -> None:
        if count % 1_000_000 == 0:
            print(f"built {count:,} publications", flush=True)

    meta = build_dblp_index(Path(args.dblp_dump), work / "dblp.sqlite", progress=built)
    print(f"index: {int(meta['records']):,} publications, fingerprint {meta['normalization_fingerprint']}", flush=True)
    shown: set[int] = set()

    def packed(done: int, total: int) -> None:
        tenth = done * 10 // max(total, 1)
        if tenth not in shown:
            shown.add(tenth)
            print(f"compressing {tenth * 10}%", flush=True)

    manifest = package_index(work / "dblp.sqlite", out, release_tag=args.release_tag, progress=packed,
                             min_records=args.min_records, probes=() if args.no_probes else PROBE_TITLES)
    Path(args.notes).write_text(release_notes(manifest), encoding="utf-8")
    print(json.dumps({"release_tag": manifest["release_tag"], "records": manifest["records"],
                      "asset": manifest["asset"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
