# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Ported from DevDocs (https://github.com/freeCodeCamp/devdocs),
# assets/javascripts/app/searcher.js, Copyright Thibaut Courouble and
# other contributors.

"""DevDocs' entry search algorithm.

Entry names are normalized (lowercased, separators collapsed to dots) and
scored first by exact substring match, then, for queries of three or more
characters, by fuzzy subsequence match. Scores run from 1 to 100; matches at
the start of the name or right after a dot score highest.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import TypeVar

T = TypeVar("T")

SEPARATOR = "."
FUZZY_MIN_LENGTH = 3

_SEPARATORS_RE = re.compile(r"#|::|:-|->|\$(?=\w)|\-(?=\w)|\:(?=\w)|\ [\/\-&]\ |:\ |\ ", re.ASCII)
_EOS_SEPARATORS_RE = re.compile(r"(\w)[\-:]$", re.ASCII)
_INFO_PARENTHESES_RE = re.compile(r"\ \(\w+?\)$", re.ASCII)
_EVENT_RE = re.compile(r"\ event$")
_DOT_RE = re.compile(r"\.+")
_WHITESPACE_RE = re.compile(r"\s")


def normalize_string(string: str) -> str:
    """Reduce a name to the form matches are made against."""
    string = string.lower().replace("...", "", 1)
    string = _EVENT_RE.sub("", string, count=1)
    string = _INFO_PARENTHESES_RE.sub("", string, count=1)
    string = _SEPARATORS_RE.sub(SEPARATOR, string)
    string = _DOT_RE.sub(SEPARATOR, string)
    string = string.replace("()", "", 1)
    return _WHITESPACE_RE.sub("", string)


def normalize_query(string: str) -> str:
    """Like `normalize_string`, but keeps a trailing separator meaningful.

    Unlike devdocs.io, where a trailing space scopes the search to a doc,
    surrounding whitespace is ignored.
    """
    return _EOS_SEPARATORS_RE.sub(r"\1.", normalize_string(string.strip()), count=1)


def _score_exact(value: str, query: str, index: int) -> int | None:
    value_length, query_length = len(value), len(query)
    # Remove one point for each unmatched character.
    score = 100 - (value_length - query_length)

    if index > 0:
        if value[index - 1] == SEPARATOR:
            # Preceded by a dot: score as if found at the start, minus one.
            score += index - 1
        elif query_length == 1:
            # A single character only matches at the start or after a dot.
            return None
        else:
            # Remove a point for each unmatched character up to the nearest
            # preceding dot, and for each unmatched character after the query.
            i = index - 2
            while i >= 0 and value[i] != SEPARATOR:
                i -= 1
            score -= (index - i) + (value_length - query_length - index)
        # Remove a point for each dot before the query, bar the one right before it.
        score -= value.count(SEPARATOR, 0, index - 1)

    # Remove five points for each dot following the query.
    score -= value.count(SEPARATOR, index + query_length) * 5
    return max(1, score)


def exact_match(value: str, query: str) -> int | None:
    index = value.find(query)
    if index < 0:
        return None
    last_index = value.rfind(query)
    score = _score_exact(value, query, index)
    if last_index != index:
        return max(score or 0, _score_exact(value, query, last_index) or 0) or None
    return score


def _score_fuzzy(value: str, match_index: int, match_length: int) -> int:
    if match_index == 0 or value[match_index - 1] == SEPARATOR:
        return max(66, 100 - match_length)
    if match_index + match_length == len(value):
        return max(33, 67 - match_length)
    return max(1, 34 - match_length)


def fuzzy_regexp(query: str) -> re.Pattern[str]:
    """`abc` -> /a.*?b.*?c/"""
    return re.compile(".*?".join(re.escape(char) for char in query))


def fuzzy_match(value: str, query: str, regexp: re.Pattern[str]) -> int | None:
    if len(value) <= len(query) or query in value:
        return None
    match = regexp.search(value)
    if match is None:
        return None
    score = _score_fuzzy(value, match.start(), match.end() - match.start())
    # Also try the last dotted segment, which usually holds the member name.
    match = regexp.search(value, value.rfind(SEPARATOR) + 1)
    if match is not None:
        return max(score, _score_fuzzy(value, match.start(), match.end() - match.start()))
    return score


def search(
    items: Sequence[T],
    texts: Sequence[str],
    query: str,
    limit: int = 50,
) -> list[tuple[T, int]]:
    """Rank `items` (whose normalized names are `texts`) against `query`.

    Exact matches come first, highest score first; fuzzy matches fill in after
    them when there are fewer than `limit` exact ones. Ties keep input order.
    """
    query = normalize_query(query)
    if not query or query == SEPARATOR:
        return []

    results = _ranked(items, texts, lambda value: exact_match(value, query), limit)
    if len(results) < limit and len(query) >= FUZZY_MIN_LENGTH:
        regexp = fuzzy_regexp(query)
        fuzzy = _ranked(items, texts, lambda value: fuzzy_match(value, query, regexp), limit)
        results.extend(fuzzy[: limit - len(results)])
    return results


def _ranked(items: Sequence[T], texts: Iterable[str], matcher, limit: int) -> list[tuple[T, int]]:
    scored = []
    for position, value in enumerate(texts):
        score = matcher(value)
        if score:
            scored.append((-round(score), position, score))
    scored.sort()
    return [(items[position], score) for _, position, score in scored[:limit]]
