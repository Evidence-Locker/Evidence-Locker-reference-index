"""Monthly distribution of the ready-made DBLP reference index.

The monthly workflow of the public repository Evidence-Locker/Evidence-Locker-reference-index
builds the index from the current DBLP dump with a verbatim copy of this module,
reference_index.py and reference_records.py (scripts/export_reference_index_builder.py), and
`package_index` turns it into the two release files:

  dblp-reference-index-<date>.sqlite.xz   the index, xz-compressed
  manifest.json                           what it is and how to check it: record count,
                                          DBLP dump SHA-256, index schema, normalization
                                          fingerprint, size and SHA-256 of both the
                                          compressed and the uncompressed file

The app downloads manifest.json from the latest release, then the index, and checks every
number before installing it (see the download step). DBLP data is CC0.
"""
from __future__ import annotations

import hashlib
import json
import lzma
import os
from pathlib import Path
from typing import Callable, Iterable

from el_offline_admission import reference_index as ri

RELEASE_REPOSITORY = "Evidence-Locker/Evidence-Locker-reference-index"
MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA = "EL_REFERENCE_INDEX_RELEASE"
MANIFEST_SCHEMA_VERSION = "1"
DATA_LICENSE = "CC0-1.0 (bibliographic data from dblp.org)"
MIN_RECORDS = 5_000_000  # the 2026-11 dump has 8.59 million publications
# Works every complete DBLP copy must find; a build that misses one is not published.
PROBE_TITLES = (
    ("Attention is All you Need", "vaswani", 2017),
    ("ImageNet Classification with Deep Convolutional Neural Networks", "krizhevsky", 2012),
    ("Mutual Information Based Matching for Causal Inference with Observational Data", "sun", 2016),
    ("METEOR: An Automatic Metric for MT Evaluation with Improved Correlation with Human Judgments", "banerjee", 2005),
)
_CHUNK = 1 << 20


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def check_index_for_release(
    index: ri.LocalReferenceIndex,
    *,
    min_records: int = MIN_RECORDS,
    probes: Iterable[tuple[str, str, int]] = PROBE_TITLES,
) -> None:
    """Refuse to publish an index that is incomplete or does not answer known lookups."""
    meta = index.meta
    if not meta.get("normalization_fingerprint"):
        raise ValueError("RELEASE_INDEX_WITHOUT_NORMALIZATION_FINGERPRINT")
    if int(meta.get("records", "0") or 0) < min_records:
        raise ValueError("RELEASE_INDEX_TOO_SMALL")
    for title, surname, year in probes:
        if not any(c.first_author == surname and c.year == year for c in index.candidates(title)):
            raise ValueError(f"RELEASE_INDEX_PROBE_NOT_FOUND:{title[:40]}")


def package_index(
    index_path: Path,
    out_dir: Path,
    *,
    release_tag: str,
    min_records: int = MIN_RECORDS,
    probes: Iterable[tuple[str, str, int]] = PROBE_TITLES,
    preset: int = 6,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Check the index, write <asset>.sqlite.xz and manifest.json into out_dir (which
    must not contain them yet) and return the manifest."""
    index_path, out_dir = Path(index_path), Path(out_dir)
    index = ri.LocalReferenceIndex(index_path)
    try:
        check_index_for_release(index, min_records=min_records, probes=probes)
        meta = dict(index.meta)
    finally:
        index.close()
    asset_name = f"dblp-reference-index-{release_tag}.sqlite.xz"
    asset_path, manifest_path = out_dir / asset_name, out_dir / MANIFEST_NAME
    for path in (asset_path, manifest_path):
        if os.path.lexists(path):
            raise FileExistsError("RELEASE_FILE_ALREADY_EXISTS")

    total = index_path.stat().st_size
    raw_digest, packed_digest = hashlib.sha256(), hashlib.sha256()
    temporary = asset_path.with_name(f".{asset_name}.incomplete")
    try:
        compressor = lzma.LZMACompressor(format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=preset)
        done = 0
        with index_path.open("rb") as source, temporary.open("xb") as target:
            for block in iter(lambda: source.read(_CHUNK), b""):
                raw_digest.update(block)
                packed = compressor.compress(block)
                packed_digest.update(packed)
                target.write(packed)
                done += len(block)
                if progress:
                    progress(done, total)
            packed = compressor.flush()
            packed_digest.update(packed)
            target.write(packed)
        os.rename(temporary, asset_path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "release_tag": release_tag,
        "source": meta.get("source", ""),
        "license": DATA_LICENSE,
        "records": int(meta["records"]),
        "dump_sha256": meta.get("dump_sha256", ""),
        "built_at_utc": meta.get("built_at_utc", ""),
        "index_schema": meta.get("schema", ""),
        "index_schema_version": meta.get("schema_version", ""),
        "normalization_fingerprint": meta["normalization_fingerprint"],
        "asset": {
            "name": asset_name,
            "compression": "xz",
            "size": asset_path.stat().st_size,
            "sha256": packed_digest.hexdigest(),
        },
        "index": {"size": total, "sha256": raw_digest.hexdigest()},
    }
    if _sha256_file(asset_path) != manifest["asset"]["sha256"]:
        raise ValueError("RELEASE_ASSET_HASH_MISMATCH")
    with manifest_path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest
