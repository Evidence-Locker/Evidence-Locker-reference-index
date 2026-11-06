"""Local reference index built from a DBLP XML dump: a third source for the reference
check that is consulted on this machine only.

Crossref and arXiv do not cover much of computer science (pre-2012 ACL Anthology, JMLR and
PMLR, recent ICLR/ICML, NIME/SMC/LREC). DBLP does, and publishes its whole database as one
XML file under CC0. The user downloads that file once; `build_dblp_index` turns it into a
small SQLite file, and `LocalReferenceIndex` answers title lookups from it. Nothing about
the references is sent anywhere by a lookup.

The index keeps, per publication: title, first-author surname, year, DOI, arXiv id (DBLP's
CoRR records) and the DBLP key. Lookups go through two 64-bit keys per title: the whole
normalized title with spaces removed (so TeX remnants such as "S imple M em" still meet
"SimpleMem"), and its first five words (subtitle present on one side only). Every
candidate is then scored by the reference check with the same rules as Crossref and
arXiv records.
"""
from __future__ import annotations

import gzip
import hashlib
import html.entities
import io
import json
import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

from el_offline_admission import reference_records as rv

INDEX_SCHEMA = "EL_DBLP_REFERENCE_INDEX"
INDEX_SCHEMA_VERSION = "2"  # 2: arXiv DOIs stored as arXiv ids
SOURCE_NAME = "dblp"
RECORD_TYPES = frozenset({"article", "inproceedings", "incollection", "book"})
PREFIX_WORDS = 5
MAX_CANDIDATES_PER_KEY = 50
_XML_BUILTIN_ENTITIES = frozenset({"amp", "lt", "gt", "quot", "apos"})
_ENTITY_RE = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
_DBLP_HOMONYM_RE = re.compile(r"\s+\d{4}$")  # "Wei Wang 0001"
_DOI_EE_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/(10\.\S+)$", re.I)
_ARXIV_EE_RE = re.compile(r"^https?://arxiv\.org/abs/(\d{4}\.\d{4,5})(?:v\d+)?$", re.I)
_FEED_BYTES = 1 << 20


def _key(prefix: str, text: str) -> int:
    digest = hashlib.blake2b(f"{prefix}:{text}".encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def title_keys(title: str) -> list[int]:
    words = rv.normalize_title(title).split()
    if not words:
        return []
    keys = [_key("t", "".join(words))]
    if len(words) >= PREFIX_WORDS:
        keys.append(_key("p", " ".join(words[:PREFIX_WORDS])))
    return keys


# Titles and names whose keys depend on every normalization rule: TeX accents and letter
# accents, braces, commands, Greek letters (TeX and Unicode), Unicode accents, punctuation,
# the five-word prefix key, DBLP homonym numbers. If an app's rules differ from the ones an
# index was built with, lookups would silently miss; the fingerprint makes that a refusal.
_FINGERPRINT_TITLES = (
    "M\\\"uller and Erd\\H{o}s on {B}ayesian \\textit{Deep} Learning",
    "$\\tau$-bench: A Benchmark for τ-Agents",
    "Ünïcödé Títlès, with: Punctuation; and (Brackets)!",
    "S imple M em: Efficient Lifelong Memory for LLM Agents",
    "On the Dangers of Stochastic Parrots: Can Language Models Be Too Big?",
)
_FINGERPRINT_NAMES = ("Jörg Müller 0002", "Smith, Alice", "Lévy, Paul", "Donald W. K. Andrews")


def normalization_fingerprint() -> str:
    probe = [[title, title_keys(title)] for title in _FINGERPRINT_TITLES]
    probe += [[name, rv._surname(_DBLP_HOMONYM_RE.sub("", name))] for name in _FINGERPRINT_NAMES]
    return hashlib.sha256(json.dumps(probe, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _replace_entity(match: re.Match) -> str:
    name = match.group(1)
    if name in _XML_BUILTIN_ENTITIES:
        return match.group(0)
    codepoint = html.entities.name2codepoint.get(name)
    return chr(codepoint) if codepoint is not None else " "


def _xml_chunks(handle: BinaryIO) -> Iterator[bytes]:
    """dblp.xml is ISO-8859-1 and uses the named entities of dblp.dtd. The DTD is not
    loaded (no external access): entities are replaced here, line by line (DBLP never
    splits one across lines), and the text is re-encoded as UTF-8."""
    pending: list[str] = []
    size = 0
    first = True
    for raw in handle:
        line = raw.decode("iso-8859-1")
        if first:
            line = re.sub(r'encoding="[^"]+"', 'encoding="UTF-8"', line, count=1)
            first = False
        line = _ENTITY_RE.sub(_replace_entity, line)
        pending.append(line)
        size += len(line)
        if size >= _FEED_BYTES:
            yield "".join(pending).encode("utf-8")
            pending, size = [], 0
    if pending:
        yield "".join(pending).encode("utf-8")


class _HashingReader(io.RawIOBase):
    """Raw reader that hashes every byte read from the dump file."""

    def __init__(self, inner: BinaryIO) -> None:
        self.inner = inner
        self.sha256 = hashlib.sha256()

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        data = self.inner.read(len(buffer))
        self.sha256.update(data)
        buffer[:len(data)] = data
        return len(data)


def _record(element: ET.Element) -> tuple | None:
    title_element = element.find("title")
    if title_element is None:
        return None
    title = " ".join("".join(title_element.itertext()).split()).rstrip(".")
    if not rv.normalize_title(title):
        return None
    author = element.findtext("author") or element.findtext("editor") or ""
    surname = rv._surname(_DBLP_HOMONYM_RE.sub("", author.strip()))
    year_text = (element.findtext("year") or "").strip()
    doi = arxiv_id = ""
    for ee in element.findall("ee"):
        link = (ee.text or "").strip()
        doi_match, arxiv_match = _DOI_EE_RE.match(link), _ARXIV_EE_RE.match(link)
        if doi_match and not doi:
            doi = doi_match.group(1)
        if arxiv_match and not arxiv_id:
            arxiv_id = arxiv_match.group(1)
    if doi.lower().startswith(rv.ARXIV_DOI_PREFIX):
        # DBLP links CoRR records through the arXiv DOI (10.48550/arXiv.2310.14935): that is
        # the preprint's arXiv id, not a DOI of a different (journal) version.
        arxiv_id, doi = arxiv_id or doi[len(rv.ARXIV_DOI_PREFIX):], ""
    return (title, surname, int(year_text) if year_text.isdigit() else None, doi, arxiv_id, element.get("key", ""))


def build_dblp_index(
    dump_path: Path,
    out_path: Path,
    *,
    progress: Callable[[int], None] | None = None,
) -> dict[str, str]:
    """Build the SQLite index from dblp.xml.gz (or dblp.xml). Refuses to overwrite.
    Returns the index metadata (record count, dump SHA-256, build time)."""
    dump_path, out_path = Path(dump_path), Path(out_path)
    if os.path.lexists(out_path):
        raise FileExistsError("REFERENCE_INDEX_ALREADY_EXISTS")
    temporary = out_path.with_name(f".{out_path.name}.incomplete")
    if os.path.lexists(temporary):
        os.unlink(temporary)
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode = OFF;
            PRAGMA synchronous = OFF;
            CREATE TABLE meta (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE works (id INTEGER PRIMARY KEY, title TEXT NOT NULL, first_author TEXT NOT NULL,
                                year INTEGER, doi TEXT NOT NULL, arxiv_id TEXT NOT NULL, dblp_key TEXT NOT NULL);
            CREATE TABLE stage (k INTEGER NOT NULL, id INTEGER NOT NULL);
            """
        )
        with dump_path.open("rb") as raw:
            hashing = _HashingReader(raw)
            buffered = io.BufferedReader(hashing, 1 << 20)
            handle = gzip.GzipFile(fileobj=buffered) if dump_path.suffix == ".gz" else buffered
            count = _load(connection, handle, progress)
            while buffered.read(1 << 20):  # hash any trailing bytes the parser did not need
                pass
        connection.executescript(
            """
            CREATE TABLE title_keys (k INTEGER NOT NULL, id INTEGER NOT NULL, PRIMARY KEY (k, id)) WITHOUT ROWID;
            INSERT OR IGNORE INTO title_keys SELECT k, id FROM stage ORDER BY k, id;
            DROP TABLE stage;
            """
        )
        meta = {
            "schema": INDEX_SCHEMA,
            "schema_version": INDEX_SCHEMA_VERSION,
            "source": SOURCE_NAME,
            "records": str(count),
            "dump_name": dump_path.name,
            "dump_sha256": hashing.sha256.hexdigest(),
            "built_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "normalization_fingerprint": normalization_fingerprint(),
        }
        connection.executemany("INSERT INTO meta VALUES (?, ?)", sorted(meta.items()))
        connection.commit()
        connection.execute("VACUUM")
        connection.close()
        connection = None
        if os.path.lexists(out_path):
            raise FileExistsError("REFERENCE_INDEX_ALREADY_EXISTS")
        os.rename(temporary, out_path)
        return meta
    finally:
        if connection is not None:
            connection.close()
        if os.path.lexists(temporary):
            os.unlink(temporary)


def _load(connection: sqlite3.Connection, handle: BinaryIO, progress: Callable[[int], None] | None) -> int:
    parser = ET.XMLPullParser(events=("start", "end"))
    depth = 0
    root: ET.Element | None = None
    works: list[tuple] = []
    keys: list[tuple[int, int]] = []
    count = 0

    def flush() -> None:
        connection.executemany("INSERT INTO works VALUES (?, ?, ?, ?, ?, ?, ?)", works)
        connection.executemany("INSERT INTO stage VALUES (?, ?)", keys)
        works.clear()
        keys.clear()

    for chunk in _xml_chunks(handle):
        parser.feed(chunk)
        for event, element in parser.read_events():
            if event == "start":
                depth += 1
                if root is None:
                    root = element
                continue
            depth -= 1
            if depth != 1:
                continue
            if element.tag in RECORD_TYPES:
                record = _record(element)
                if record is not None:
                    count += 1
                    works.append((count, *record))
                    keys.extend((key, count) for key in title_keys(record[0]))
                    if len(works) >= 50_000:
                        flush()
                        if progress:
                            progress(count)
            assert root is not None
            root.clear()
    parser.close()
    flush()
    return count


class LocalReferenceIndex:
    """Read-only lookups in an index built by build_dblp_index. No network access."""

    source = SOURCE_NAME

    def __init__(self, path: Path) -> None:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError("REFERENCE_INDEX_NOT_FOUND")
        self._connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            self.meta = dict(self._connection.execute("SELECT name, value FROM meta"))
        except sqlite3.DatabaseError as exc:
            self._connection.close()
            raise ValueError("REFERENCE_INDEX_UNREADABLE") from exc
        if self.meta.get("schema") != INDEX_SCHEMA or self.meta.get("schema_version") != INDEX_SCHEMA_VERSION:
            self._connection.close()
            raise ValueError("REFERENCE_INDEX_SCHEMA_MISMATCH")
        built_with = self.meta.get("normalization_fingerprint")
        if built_with is not None and built_with != normalization_fingerprint():
            # Indexes built before the fingerprint existed carry none and used these rules.
            self._connection.close()
            raise ValueError("REFERENCE_INDEX_NORMALIZATION_MISMATCH")

    def candidates(self, title: str) -> list[rv.Candidate]:
        found: dict[int, rv.Candidate] = {}
        for key in title_keys(title):
            rows = self._connection.execute(
                "SELECT w.id, w.title, w.first_author, w.year, w.doi, w.arxiv_id FROM title_keys k "
                "JOIN works w ON w.id = k.id WHERE k.k = ? LIMIT ?",
                (key, MAX_CANDIDATES_PER_KEY),
            )
            for row_id, row_title, author, year, doi, arxiv_id in rows:
                found.setdefault(row_id, rv.Candidate(
                    source=SOURCE_NAME, title=row_title, first_author=author, year=year, doi=doi, arxiv_id=arxiv_id))
        return list(found.values())

    def close(self) -> None:
        self._connection.close()
