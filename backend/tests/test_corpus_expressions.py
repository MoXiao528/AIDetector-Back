import hashlib
import json
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import jieba
import pytest

from app.services import corpus_expressions
from app.services.corpus_expressions import LexiconMatcher


_SHA = "ea2793dd9b6bbc045194ccd8db8ab018006519dd961629cc879cd0a9cfe80ced"


def _unexpected_call(*args, **kwargs):
    raise AssertionError("Unexpected cache initialization or runtime file read")


def test_packaged_matcher_preserves_domain_rank_longest_match_and_deduplication(
    monkeypatch,
):
    monkeypatch.setattr(jieba.Tokenizer, "initialize", _unexpected_call)
    matcher = LexiconMatcher()
    text = "值得关注的是，进一步研究仍很重要。这不仅仅是新的研究成果。" * 2
    expected = {
        "academic": ["重要", "进一步", "值得关注", "研究成果"],
        "news": ["重要", "进一步", "不仅仅是", "值得关注的是"],
        "novel": ["值得", "这不仅仅是"],
        "seo": ["值得", "重要", "关注", "不仅仅是", "进一步"],
        "webtext": ["进一步", "这不仅仅是", "值得关注"],
        "wiki": ["重要", "进一步", "关注", "值得"],
    }

    with monkeypatch.context() as runtime_patch:
        runtime_patch.setattr(Path, "read_bytes", _unexpected_call)
        runtime_patch.setattr(Path, "read_text", _unexpected_call)
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                domain: executor.submit(
                    matcher.match, text, language="zh", domain=domain
                )
                for domain in expected
            }
            results = {domain: future.result() for domain, future in futures.items()}
        separated = matcher.match("值得２０２６关注的是", language="zh", domain="news")
        no_match = matcher.match("123 abc", language="zh", domain="news")
        with monkeypatch.context() as invalid_route_patch:
            invalid_route_patch.setattr(jieba.Tokenizer, "cut", _unexpected_call)
            invalid_routes = [
                matcher.match(text, language=language, domain=domain)
                for language, domain in (
                    ("en", "news"),
                    ("zh", "medical"),
                    (None, None),
                )
            ]

    def payload(status, expressions):
        return {
            "status": status,
            "lexiconVersion": "1.0.0",
            "lexiconSha256": _SHA,
            "expressions": expressions,
        }

    for domain, result in results.items():
        assert {
            key: value for key, value in result.items() if key != "matches"
        } == payload("matched", expected[domain])
        assert [match["expression"] for match in result["matches"]] == expected[domain]
        for match in result["matches"]:
            offsets = [(offset["start"], offset["end"]) for offset in match["offsets"]]
            assert len(offsets) == 2
            assert offsets == sorted(set(offsets))
            assert all(text[start:end] == match["expression"] for start, end in offsets)
    assert separated == {
        **payload("matched", ["值得", "关注的是"]),
        "matches": [
            {"expression": "值得", "offsets": [{"start": 0, "end": 2}]},
            {"expression": "关注的是", "offsets": [{"start": 6, "end": 10}]},
        ],
    }
    assert no_match == payload("no_match", [])
    assert invalid_routes == [payload("unavailable", [])] * 3


def test_offsets_use_original_code_points_for_emoji_crlf_and_compatibility_han():
    matcher = LexiconMatcher()
    text = "😀㎏\r\n值得关注的是！不仅仅是\r\n值得关注的是"
    result = matcher.match(text, language="zh", domain="news")
    assert result["expressions"] == ["不仅仅是", "值得关注的是"]
    assert result["matches"] == [
        {"expression": "不仅仅是", "offsets": [{"start": 11, "end": 15}]},
        {
            "expression": "值得关注的是",
            "offsets": [{"start": 4, "end": 10}, {"start": 17, "end": 23}],
        },
    ]
    assert text[11:15] == "不仅仅是"
    assert unicodedata.normalize("NFKC", text[11:15]) == "不仅仅是"

    composed = matcher.match("e\u0301" + text, language="zh", domain="news")
    assert composed == {key: value for key, value in result.items() if key != "matches"}


def test_whole_compatibility_expansion_maps_but_its_inner_boundary_does_not(
    monkeypatch,
):
    matcher = LexiconMatcher()
    monkeypatch.setattr(
        matcher._tokenizer, "cut", lambda text, HMM: iter(("株式", "会社"))
    )
    table = {("株式", "会社"): ("株式会社", 1), ("会社",): ("会社", 2)}
    monkeypatch.setitem(matcher._tables, "news", table)
    text = "😀㍿，㍿"
    result = matcher.match(text, language="zh", domain="news")
    assert result["expressions"] == ["株式会社"]
    assert result["matches"] == [
        {
            "expression": "株式会社",
            "offsets": [{"start": 1, "end": 2}, {"start": 3, "end": 4}],
        }
    ]

    table.pop(("株式", "会社"))
    result = matcher.match(text, language="zh", domain="news")
    assert result["status"] == "matched"
    assert result["expressions"] == ["会社"]
    assert "matches" not in result


def test_lexicon_loader_rejects_missing_or_changed_asset(tmp_path):
    manifest = corpus_expressions._ASSET_DIR / "manifest.json"
    copied_manifest = tmp_path / manifest.name
    copied_manifest.write_bytes(manifest.read_bytes())
    with pytest.raises(FileNotFoundError):
        LexiconMatcher(tmp_path)

    raw = (corpus_expressions._ASSET_DIR / "lexicon.json").read_bytes()
    changed = raw + b"\n"
    (tmp_path / "lexicon.json").write_bytes(changed)
    with pytest.raises(ValueError, match="Lexicon bytes or SHA mismatch"):
        LexiconMatcher(tmp_path)

    rewritten = json.loads(manifest.read_bytes())
    rewritten["artifacts"]["lexicon.json"] = {
        "bytes": len(changed),
        "sha256": hashlib.sha256(changed).hexdigest(),
    }
    copied_manifest.write_text(json.dumps(rewritten), encoding="utf-8")
    with pytest.raises(ValueError, match="Lexicon bytes or SHA mismatch"):
        LexiconMatcher(tmp_path)


def test_lexicon_loader_checks_installed_dictionary(monkeypatch):
    read_bytes = Path.read_bytes

    def substitute_dictionary(path):
        return (
            b"wrong dictionary bytes" if path.name == "dict.txt" else read_bytes(path)
        )

    monkeypatch.setattr(Path, "read_bytes", substitute_dictionary)
    with pytest.raises(ValueError, match="Tokenizer dictionary SHA mismatch"):
        LexiconMatcher()
