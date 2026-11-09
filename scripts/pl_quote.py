"""Tokenising and quote location for athetize: find a quoted passage inside a source text.

Standard library only.
"""

from __future__ import annotations

import bisect
import difflib
import re

TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def tokenize(text: str):
    return [(m.group().casefold(), m.start(), m.end()) for m in TOKEN_RE.finditer(text)]


def find_span(haystack: str, needle: str):
    """Find `needle` in `haystack`, tolerant of whitespace differences.

    Returns (start, end) in `haystack` or None. Used for claim spans in paragraphs.
    """
    i = haystack.find(needle)
    if i >= 0:
        return i, i + len(needle)
    words = needle.split()
    if not words:
        return None
    pattern = r"\s+".join(re.escape(w) for w in words)
    m = re.search(pattern, haystack)
    if m:
        return m.start(), m.end()
    return None


_NEGATIONS = {"no", "not", "nor", "never", "without", "neither", "none", "cannot", "nt", "non"}


def _locate_exact(s_tokens: list, words: list[str], q_joined: str):
    """Stage 1: the quote's words appear in the source in the same order, on word boundaries."""
    joined = " ".join(words)
    starts = []
    pos = 0
    for w in words:
        starts.append(pos)
        pos += len(w) + 1
    idx = joined.find(q_joined)
    while idx >= 0:
        end_idx = idx + len(q_joined)
        at_start = idx == 0 or joined[idx - 1] == " "
        at_end = end_idx == len(joined) or joined[end_idx] == " "
        if at_start and at_end:
            first = bisect.bisect_right(starts, idx) - 1
            last = bisect.bisect_right(starts, end_idx - 1) - 1
            return {
                "start": s_tokens[first][1],
                "end": s_tokens[last][2],
                "mode": "exact",
                "score": 1.0,
            }
        idx = joined.find(q_joined, idx + 1)
    return None


def _locate_joined(s_tokens: list, words: list[str], q_tokens: list[str]):
    """Stage 2: the quote's letters appear in the source once all spaces are ignored."""
    sf = "".join(words)
    q_sf = "".join(q_tokens)
    if len(q_sf) < 24:
        return None
    cum = []
    total = 0
    for w in words:
        cum.append(total)
        total += len(w)
    j = sf.find(q_sf)
    if j < 0:
        return None
    first = bisect.bisect_right(cum, j) - 1
    last = bisect.bisect_right(cum, j + len(q_sf) - 1) - 1
    return {
        "start": s_tokens[first][1],
        "end": s_tokens[last][2],
        "mode": "joined",
        "score": 1.0,
    }


def _trigram_votes(words: list[str], q_tokens: list[str]) -> dict[int, int]:
    """Count, for each implied window start in the source, how many quote trigrams point at it."""
    n = len(q_tokens)
    tri: dict[tuple, list[int]] = {}
    for j in range(n - 2):
        tri.setdefault(tuple(q_tokens[j : j + 3]), []).append(j)
    votes: dict[int, int] = {}
    for i in range(len(words) - 2):
        key = (words[i], words[i + 1], words[i + 2])
        if key in tri:
            for j in tri[key]:
                votes[i - j] = votes.get(i - j, 0) + 1
    return votes


def _candidate_windows(votes: dict[int, int], n: int, n_words: int):
    """Yield (a, b) word ranges to compare with the quote: the six best-voted starts, each tried
    at the quote's length and one or two words longer or shorter."""
    for start_idx, _ in sorted(votes.items(), key=lambda kv: -kv[1])[:6]:
        for delta in (0, 1, -1, 2, -2):
            a = max(0, start_idx)
            b = min(n_words, start_idx + n + delta)
            if b - a < max(4, n - 3):
                continue
            yield a, b


def _keeps_numbers_and_negations(window: list[str], q_nums: list[str], q_neg: int) -> bool:
    """True when the window has exactly the quote's numbers and the same count of negations."""
    # A near match must never change a number or flip a negation.
    if sorted(t for t in window if any(ch.isdigit() for ch in t)) != q_nums:
        return False
    if sum(1 for t in window if t in _NEGATIONS) != q_neg:
        return False
    return True


def _best_fuzzy_window(words: list[str], q_tokens: list[str], q_joined: str, votes: dict):
    """Return (ratio, a, b) for the candidate window most similar to the quote, or None."""
    best = None
    q_nums = sorted(t for t in q_tokens if any(ch.isdigit() for ch in t))
    q_neg = sum(1 for t in q_tokens if t in _NEGATIONS)
    for a, b in _candidate_windows(votes, len(q_tokens), len(words)):
        window = words[a:b]
        if not _keeps_numbers_and_negations(window, q_nums, q_neg):
            continue
        cand = " ".join(window)
        ratio = difflib.SequenceMatcher(None, q_joined, cand, autojunk=False).ratio()
        if best is None or ratio > best[0]:
            best = (ratio, a, b)
    return best


def _locate_fuzzy(
    s_tokens: list, words: list[str], q_tokens: list[str], q_joined: str, fuzzy_threshold: float
):
    """Stage 3: near-exact match, found by voting on window starts using quote trigrams."""
    if len(q_tokens) < 6:
        return None
    votes = _trigram_votes(words, q_tokens)
    best = _best_fuzzy_window(words, q_tokens, q_joined, votes)
    if best and best[0] >= fuzzy_threshold:
        ratio, a, b = best
        return {
            "start": s_tokens[a][1],
            "end": s_tokens[b - 1][2],
            "mode": "fuzzy",
            "score": round(ratio, 3),
        }
    return None


def locate_quote(source: str, quote: str, fuzzy_threshold: float = 0.94):
    """Locate `quote` inside `source`.

    Returns dict(start, end, mode, score) or None. Modes:
      exact  - same words in the same order (ignores case, punctuation, spacing)
      joined - same letters ignoring spaces (handles hyphenation/line breaks)
      fuzzy  - near-exact (>= fuzzy_threshold similarity)
    """
    q_tokens = [t for t, _, _ in tokenize(quote)]
    if len(q_tokens) < 4:
        return None
    s_tokens = tokenize(source)
    if not s_tokens:
        return None
    words = [t for t, _, _ in s_tokens]
    q_joined = " ".join(q_tokens)

    # 1. exact token sequence
    found = _locate_exact(s_tokens, words, q_joined)
    if found:
        return found
    # 2. letters only, ignoring spaces
    found = _locate_joined(s_tokens, words, q_tokens)
    if found:
        return found
    # 3. fuzzy: vote on implied window starts using quote trigrams
    return _locate_fuzzy(s_tokens, words, q_tokens, q_joined, fuzzy_threshold)
