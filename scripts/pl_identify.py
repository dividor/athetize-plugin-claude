"""Work out which reference a supplied file belongs to, from what the file itself says.

People save papers under whatever name the publisher or their browser gives them, so the
file name is the last thing looked at. A file is identified by the DOI or arXiv number
printed on its opening pages, or by the reference's title appearing there as one unbroken
run of words together with the first author's surname.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

import pl_refmatch
import pl_text

# Characters from the start of a file that are read as its title page.
_FRONT = 6000
# Share of a reference title that must appear as one unbroken run to count as the file's title.
_TITLE_RUN = 0.85
# Titles shorter than this (after normalising) are too common to identify a file on their own.
_MIN_TITLE = 12


def _ref_title(ref: dict) -> str:
    t = re.sub(r"\s*\([^)]*$", "", (ref.get("title") or "").strip())
    if not t:
        m = re.search(
            r"\(\d{4}[a-z]?\)\.\s*(.+?)[.?]\s",
            re.sub(r"\s*\((?:[^()]*?(?:Trans|Eds?|ed)\.[^()]*)\)", "", ref.get("raw") or ""),
        )
        t = m.group(1) if m else ""
    return t


def _ref_year(ref: dict) -> str:
    if ref.get("year"):
        return str(ref["year"])
    m = re.search(r"\((\d{4})", ref.get("raw") or "")
    return m.group(1) if m else ""


def with_known_doi(ref: dict, entry: dict) -> dict:
    """The reference, with the DOI a citation database confirmed when the paper gave none."""
    if ref.get("doi") or entry.get("exists") != "verified" or not entry.get("doi"):
        return ref
    return dict(ref, doi=entry["doi"])


class _Front:
    """The opening of a file, prepared once so every reference can be compared with it."""

    def __init__(self, text: str):
        opening = text[:_FRONT]
        self.text = pl_refmatch._fold(opening)
        self._words = pl_text.norm_title(opening)
        self._matcher = difflib.SequenceMatcher(None, autojunk=False)
        self._matcher.set_seq2(self._words)

    def title_run(self, title: str) -> float:
        """Share of the title that appears as one unbroken run of words in the opening."""
        wanted = pl_text.norm_title(title)
        if len(wanted) < _MIN_TITLE:
            return 0.0
        self._matcher.set_seq1(wanted)
        block = self._matcher.find_longest_match(0, len(wanted), 0, len(self._words))
        return block.size / len(wanted)

    def has_identifier(self, ref: dict) -> bool:
        """True when the reference's DOI or arXiv number is printed in the opening."""
        doi = pl_refmatch.normalize_doi(ref.get("doi"))
        if doi and re.search(re.escape(doi) + r"(?![0-9a-z])", self.text):
            return True
        arxiv = re.search(r"\d{4}\.\d{4,5}", ref.get("arxiv") or "")
        if not arxiv:
            return False
        return (
            re.search(r"arxiv:\s*" + re.escape(arxiv.group(0)) + r"(?!\d)", self.text) is not None
        )


def _content_score(ref: dict, front: _Front, stem: str, fname: str) -> float | None:
    """Score how well a file fits one reference; None when nothing identifies it as that work.

    A printed DOI or arXiv number identifies the file by itself. A title, in the text or in
    the file name, only counts when the first author's surname is there too.
    """
    title = _ref_title(ref)
    identified = front.has_identifier(ref)
    titled = front.title_run(title) >= _TITLE_RUN
    named = bool(title) and pl_text.title_similarity(title, fname) >= 0.6
    sur = pl_refmatch.first_surname(ref)
    has_author = (not sur) or (sur in front.text) or (sur in stem)
    if not (identified or ((titled or named) and has_author)):
        return None
    yr = _ref_year(ref)
    return (
        (6 if identified else 0)
        # a longer title is the more specific match when one title begins with another
        + (4 + min(len(title), 200) / 1000 if titled else 0)
        + (2 if named else 0)
        + (1 if has_author else 0)
        + (0.5 if yr and (yr in front.text or yr in stem) else 0)
    )


def _match_by_filename(stem: str, targets: dict[str, dict]) -> str | None:
    """The reference id the file was named after; longest id first.

    The name must equal the id or end in ``__<id>``. A name that merely starts with the id
    counts only for ids that are not plain numbers: publishers name files things like
    ``1-s2.0-S0019103508.pdf``, which says nothing about reference 1.
    """
    for rid in sorted(targets, key=len, reverse=True):
        r = rid.casefold()
        if stem == r or stem.endswith("__" + r):
            return rid
        if not r.isdigit() and re.match(re.escape(r) + r"([^a-z0-9]|$)", stem):
            return rid
    return None


def _match_file_to_ref(
    path: Path, text: str, targets: dict[str, dict]
) -> tuple[str | None, str, list[str]]:
    """Return (ref_id, how, ambiguous_ids).

    What the file says wins. Its name is used only when the text does not settle it.
    """
    stem = path.stem.casefold()
    fname = re.sub(r"[_\-]+", " ", stem)
    front = _Front(text)
    scored = []
    for rid, ref in targets.items():
        score = _content_score(ref, front, stem, fname)
        if score is not None:
            scored.append((score, rid))
    scored.sort(reverse=True)
    top = [rid for score, rid in scored if score == scored[0][0]]
    if len(top) == 1:
        return top[0], "content", []
    by_name = _match_by_filename(stem, targets)
    if by_name is not None:
        return by_name, "filename", []
    if top:
        return None, "ambiguous", top
    return None, "no match", []
