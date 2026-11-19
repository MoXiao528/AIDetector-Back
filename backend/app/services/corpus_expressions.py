"""Match the locally packaged corpus expressions against an existing route."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from importlib.metadata import version
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jieba import Tokenizer


_ASSET_DIR = Path(__file__).resolve().parents[1] / "assets" / "corpus_expressions"
_DOMAINS = {"news", "academic", "webtext", "wiki", "novel", "seo"}
_VERSION = "1.0.0"
_SHA256 = "ea2793dd9b6bbc045194ccd8db8ab018006519dd961629cc879cd0a9cfe80ced"
_JIEBA_VERSION = "0.42.1"
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _load_tokenizer(expected_sha: str) -> Tokenizer:
    import jieba

    _require(version("jieba") == _JIEBA_VERSION, "Unsupported jieba version")
    dictionary_path = Path(jieba.__file__).with_name("dict.txt")
    dictionary = dictionary_path.read_bytes()
    _require(
        hashlib.sha256(dictionary).hexdigest() == expected_sha,
        "Tokenizer dictionary SHA mismatch",
    )
    tokenizer = jieba.Tokenizer(str(dictionary_path))
    # Pinned jieba can initialize directly from the verified bytes without using a shared cache.
    tokenizer.FREQ, tokenizer.total = tokenizer.gen_pfdict(BytesIO(dictionary))
    tokenizer.initialized = True
    return tokenizer


def _original_matches(
    text: str,
    normalized: str,
    expressions: list[str],
    spans: dict[str, list[tuple[int, int]]],
) -> list[dict] | None:
    boundaries: range | list[int | None]
    if text == normalized:
        boundaries = range(len(text) + 1)
    else:
        units = [unicodedata.normalize("NFKC", char) for char in text]
        if "".join(units) != normalized:
            return None
        # Only whole original code points have boundaries within a compatibility expansion.
        boundaries = [None] * (len(normalized) + 1)
        boundaries[0] = 0
        position = 0
        for index, unit in enumerate(units, 1):
            position += len(unit)
            boundaries[position] = index

    matches = []
    for expression in expressions:
        offsets = []
        for start, end in spans[expression]:
            original_start, original_end = boundaries[start], boundaries[end]
            if original_start is None or original_end is None:
                return None
            if (
                unicodedata.normalize("NFKC", text[original_start:original_end])
                != expression
            ):
                return None
            offsets.append({"start": original_start, "end": original_end})
        matches.append({"expression": expression, "offsets": offsets})
    return matches


class LexiconMatcher:
    def __init__(self, directory: Path = _ASSET_DIR) -> None:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        _require(
            isinstance(manifest, dict)
            and manifest.get("status") == "complete"
            and manifest.get("lexicon_version") == _VERSION
            and manifest.get("production_eligible") is False,
            "Incomplete or unsupported lexicon manifest",
        )
        artifacts = manifest.get("artifacts")
        _require(isinstance(artifacts, dict), "Missing artifact bindings")
        binding = artifacts.get("lexicon.json")
        _require(isinstance(binding, dict), "Missing lexicon binding")
        raw = (directory / "lexicon.json").read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        _require(
            type(binding.get("bytes")) is int
            and binding["bytes"] == len(raw)
            and binding.get("sha256") == sha == _SHA256,
            "Lexicon bytes or SHA mismatch",
        )
        lexicon = json.loads(raw)
        _require(
            isinstance(lexicon, dict)
            and type(lexicon.get("schema_version")) is int
            and lexicon["schema_version"] == 1
            and lexicon.get("lexicon_version") == _VERSION
            and lexicon.get("language") == "zh"
            and lexicon.get("production_eligible") is False,
            "Unsupported lexicon contract",
        )
        dictionary_sha = manifest.get("dictionary_sha256")
        tokenizer = lexicon.get("tokenizer")
        _require(
            isinstance(dictionary_sha, str)
            and re.fullmatch(r"[0-9a-f]{64}", dictionary_sha) is not None
            and isinstance(tokenizer, dict)
            and tokenizer.get("name") == "jieba"
            and tokenizer.get("version") == _JIEBA_VERSION
            and tokenizer.get("hmm") is False
            and tokenizer.get("dictionary_sha256") == dictionary_sha,
            "Invalid tokenizer contract",
        )
        domains = lexicon.get("domains")
        _require(
            isinstance(domains, dict) and set(domains) == _DOMAINS,
            "Expected all six domains",
        )
        self._tables = {}
        for domain, rows in domains.items():
            _require(isinstance(rows, list), "Invalid domain entries")
            table = {}
            for rank, row in enumerate(rows, 1):
                _require(
                    isinstance(row, dict)
                    and set(row) == {"kind", "tokens", "expression", "rank"},
                    "Invalid term entry fields",
                )
                tokens, expression, kind = row["tokens"], row["expression"], row["kind"]
                _require(
                    type(row["rank"]) is int and row["rank"] == rank,
                    "Invalid term rank",
                )
                _require(
                    isinstance(tokens, list)
                    and 1 <= len(tokens) <= 4
                    and all(
                        isinstance(token, str) and _HAN.fullmatch(token)
                        for token in tokens
                    ),
                    "Invalid Han tokens",
                )
                _require(
                    isinstance(expression, str)
                    and expression == "".join(tokens)
                    and unicodedata.normalize("NFKC", expression) == expression,
                    "Expression does not match normalized tokens",
                )
                _require(
                    (kind == "word" and len(tokens) == 1 and 2 <= len(expression) <= 8)
                    or (
                        kind == "phrase"
                        and 2 <= len(tokens) <= 4
                        and 4 <= len(expression) <= 16
                    ),
                    "Unsupported term boundaries",
                )
                key = tuple(tokens)
                _require(key not in table, "Duplicate token sequence")
                table[key] = (expression, rank)
            self._tables[domain] = table
        self._tokenizer = _load_tokenizer(dictionary_sha)
        self.version = _VERSION
        self.sha256 = sha

    def match(self, text: str, *, language: str | None, domain: str | None) -> dict:
        _require(
            isinstance(text, str) and len(text) <= 20_000,
            "Text must be a string of at most 20000 characters",
        )
        result = {
            "status": "unavailable",
            "lexiconVersion": self.version,
            "lexiconSha256": self.sha256,
            "expressions": [],
        }
        if (
            language != "zh"
            or not isinstance(domain, str)
            or domain not in self._tables
        ):
            return result

        table = self._tables[domain]
        found: dict[str, int] = {}
        spans: dict[str, list[tuple[int, int]]] = {}
        normalized = unicodedata.normalize("NFKC", text)
        for run in _HAN.finditer(normalized):
            tokens = tuple(self._tokenizer.cut(run.group(), HMM=False))
            _require(
                all(
                    isinstance(token, str) and _HAN.fullmatch(token) for token in tokens
                )
                and "".join(tokens) == run.group(),
                "Tokenizer changed a Han run",
            )
            index = 0
            start = run.start()
            while index < len(tokens):
                for size in range(min(4, len(tokens) - index), 0, -1):
                    item = table.get(tokens[index : index + size])
                    if item is not None:
                        expression, rank = item
                        found[expression] = min(found.get(expression, rank), rank)
                        end = start + len(expression)
                        spans.setdefault(expression, []).append((start, end))
                        start = end
                        index += size
                        break
                else:
                    start += len(tokens[index])
                    index += 1
        if not found:
            result["status"] = "no_match"
            return result
        expressions = sorted(
            found, key=lambda expression: (found[expression], expression)
        )
        result["expressions"] = expressions
        result["status"] = "matched"
        matches = _original_matches(text, normalized, expressions, spans)
        if matches is not None:
            result["matches"] = matches
        return result
