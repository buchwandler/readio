"""Deterministic, source-span-preserving alignment of text and ASR words."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .text import normalize_text
from .types import TranscriptWord

AlignmentKind = Literal["equal", "substitution", "deletion", "insertion"]
_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", flags=re.UNICODE)


@dataclass(frozen=True, slots=True)
class SourceWord:
    raw: str
    normalized: str
    char_start: int
    char_end: int


@dataclass(frozen=True, slots=True)
class RecognizedWord:
    raw: str
    normalized: str
    start_seconds: float
    end_seconds: float
    word_index: int


@dataclass(frozen=True, slots=True)
class AlignmentOperation:
    kind: AlignmentKind
    source: SourceWord | None
    recognized: RecognizedWord | None


@dataclass(frozen=True, slots=True)
class AlignmentResult:
    source_words: tuple[SourceWord, ...]
    recognized_words: tuple[RecognizedWord, ...]
    operations: tuple[AlignmentOperation, ...]
    matched_words: int
    substitutions: int
    deletions: int
    insertions: int
    coverage: float

    @property
    def expected_words(self) -> int:
        return len(self.source_words)

    @property
    def recognized_count(self) -> int:
        return len(self.recognized_words)


def tokenize_source(text: str) -> tuple[SourceWord, ...]:
    """Tokenize source text while retaining exact half-open character spans."""
    result: list[SourceWord] = []
    for match in _WORD_RE.finditer(text):
        normalized = normalize_text(match.group()).split()
        result.extend(
            SourceWord(
                raw=match.group(),
                normalized=token,
                char_start=match.start(),
                char_end=match.end(),
            )
            for token in normalized
        )
    return tuple(result)


def _recognized_tokens(words: tuple[TranscriptWord, ...]) -> tuple[RecognizedWord, ...]:
    result: list[RecognizedWord] = []
    for index, word in enumerate(words):
        normalized_text = normalize_text(word.text)
        for match in _WORD_RE.finditer(normalized_text):
            result.append(
                RecognizedWord(
                    raw=match.group(),
                    normalized=match.group(),
                    start_seconds=word.start_seconds,
                    end_seconds=word.end_seconds,
                    word_index=index,
                )
            )
    return tuple(result)


def align_words(text: str, words: tuple[TranscriptWord, ...]) -> AlignmentResult:
    """Align ASR word tokens to exact source spans using deterministic edit distance."""
    source = tokenize_source(text)
    recognized = _recognized_tokens(words)
    rows, columns = len(source), len(recognized)
    costs = [[0] * (columns + 1) for _ in range(rows + 1)]
    for row in range(1, rows + 1):
        costs[row][0] = row
    for column in range(1, columns + 1):
        costs[0][column] = column
    for row in range(1, rows + 1):
        for column in range(1, columns + 1):
            substitution = (
                0 if source[row - 1].normalized == recognized[column - 1].normalized else 1
            )
            costs[row][column] = min(
                costs[row - 1][column] + 1,
                costs[row][column - 1] + 1,
                costs[row - 1][column - 1] + substitution,
            )

    operations: list[AlignmentOperation] = []
    row, column = rows, columns
    while row or column:
        if (
            row
            and column
            and source[row - 1].normalized == recognized[column - 1].normalized
            and costs[row][column] == costs[row - 1][column - 1]
        ):
            operations.append(AlignmentOperation("equal", source[row - 1], recognized[column - 1]))
            row -= 1
            column -= 1
        elif row and column and costs[row][column] == costs[row - 1][column - 1] + 1:
            operations.append(
                AlignmentOperation("substitution", source[row - 1], recognized[column - 1])
            )
            row -= 1
            column -= 1
        elif row and costs[row][column] == costs[row - 1][column] + 1:
            operations.append(AlignmentOperation("deletion", source[row - 1], None))
            row -= 1
        else:
            operations.append(AlignmentOperation("insertion", None, recognized[column - 1]))
            column -= 1
    operations.reverse()
    matched = sum(item.kind == "equal" for item in operations)
    substitutions = sum(item.kind == "substitution" for item in operations)
    deletions = sum(item.kind == "deletion" for item in operations)
    insertions = sum(item.kind == "insertion" for item in operations)
    coverage = matched / rows if rows else (1.0 if not columns else 0.0)
    return AlignmentResult(
        source_words=source,
        recognized_words=recognized,
        operations=tuple(operations),
        matched_words=matched,
        substitutions=substitutions,
        deletions=deletions,
        insertions=insertions,
        coverage=coverage,
    )
