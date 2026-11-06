"""Bibliographic records and title normalization shared by the reference check and the
reference index.

Standalone (standard library only): this module, reference_index.py and
reference_index_release.py are also published verbatim, as the builder of the monthly
reference index, in the public repository Evidence-Locker/Evidence-Locker-reference-index
(see scripts/export_reference_index_builder.py). The index stores title keys computed with
normalize_title, so the app and the builder must use the same rules; the index carries a
normalization fingerprint and the app refuses an index built with other rules.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

ARXIV_DOI_PREFIX = "10.48550/arxiv."


@dataclass(frozen=True)
class Candidate:
    """One record found in a source (Crossref, arXiv, a local index)."""

    source: str
    title: str
    first_author: str = ""
    year: int | None = None
    doi: str = ""
    arxiv_id: str = ""


_TEX_COMMAND_RE = re.compile(r"\\[A-Za-z]+\*?|\\.")
_TEX_ACCENT_RE = re.compile(r"\\[\"'`^~=.]")
_TEX_LETTER_ACCENT_RE = re.compile(r"\\[HcvukrbdtL]\s*\{\s*([A-Za-z])\s*\}")
_GREEK = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ζ": "zeta", "η": "eta",
    "θ": "theta", "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "π": "pi",
    "ρ": "rho", "σ": "sigma", "τ": "tau", "υ": "upsilon", "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
}
_TEX_GREEK_RE = re.compile(r"\\(" + "|".join(sorted(set(_GREEK.values()), key=len, reverse=True)) + r")(?![A-Za-z])", re.I)


def normalize_title(text: str) -> str:
    text = _TEX_GREEK_RE.sub(lambda match: " " + match.group(1).lower() + " ", text or "")  # $\tau$-bench -> tau bench
    text = "".join(f" {_GREEK[ch.lower()]} " if ch.lower() in _GREEK else ch for ch in text)  # τ-bench -> tau bench
    text = _TEX_LETTER_ACCENT_RE.sub(r"\1", text)  # Erd\H{o}s -> Erdos
    text = _TEX_ACCENT_RE.sub("", text)  # M\"uller -> Muller, not "M uller"
    text = re.sub(r"\\[A-Za-z]+\*?\s*(?=\{)", " ", text)  # \\textit{Deep} -> keep "Deep"
    text = text.replace("{", "").replace("}", "")  # BibTeX braces protect case, they do not split words
    text = _TEX_COMMAND_RE.sub(" ", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _surname(name: str) -> str:
    name = normalize_title(name.split(",")[0] if "," in name else (name.split()[-1] if name.split() else ""))
    return name.split()[-1] if name.split() else ""
