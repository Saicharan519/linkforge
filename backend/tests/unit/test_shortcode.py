import re

import pytest

from app.core import shortcode
from app.core.shortcode import InvalidAlias, generate_code, validate_alias

RESERVED_WORDS = (
    "api healthz readyz metrics docs openapi.json login register dashboard assets static admin"
)


@pytest.mark.parametrize(
    "alias",
    ["abc", "Abc-123_x", "my_link", "A" * 32, "123", "a-b", "_-_"],
)
def test_alias_rules_accepts(alias: str) -> None:
    assert validate_alias(alias) == alias


@pytest.mark.parametrize(
    "alias",
    [
        "",
        "ab",  # too short
        "a" * 33,  # too long
        "has space",
        " abc",
        "abc ",
        "abc\n",  # a trailing newline must not slip through a "$" anchor
        "dot.com",
        "a/b",
        "emoji😀",
        "tab\tbed",
    ],
)
def test_alias_rules_rejects(alias: str) -> None:
    with pytest.raises(InvalidAlias):
        validate_alias(alias)


def test_alias_case_preserved() -> None:
    assert validate_alias("MyLink") == "MyLink"
    assert validate_alias("mylink") == "mylink"  # a different alias: codes are case-sensitive


@pytest.mark.parametrize("word", [w for w in RESERVED_WORDS.split() if "." not in w])
def test_reserved_aliases(word: str) -> None:
    with pytest.raises(InvalidAlias, match="reserved"):
        validate_alias(word)


@pytest.mark.parametrize("word", ["API", "Admin", "LOGIN", "HealthZ", "Dashboard", "ASSETS"])
def test_reserved_alias_case_insensitive(word: str) -> None:
    with pytest.raises(InvalidAlias, match="reserved"):
        validate_alias(word)


def test_reserved_list_matches_architecture() -> None:
    assert shortcode.RESERVED == frozenset(RESERVED_WORDS.split())


def test_reserved_words_only_block_exact_matches() -> None:
    assert validate_alias("apis") == "apis"
    assert validate_alias("docs1") == "docs1"
    assert validate_alias("my-admin") == "my-admin"


def test_random_code_shape() -> None:
    pattern = re.compile(r"[A-Za-z0-9]{7}")
    codes = {generate_code() for _ in range(2000)}
    assert all(pattern.fullmatch(c) for c in codes)
    # 2000 draws from 62**7 values: a duplicate would mean the generator is badly broken.
    assert len(codes) == 2000


def test_random_code_skips_reserved(monkeypatch: pytest.MonkeyPatch) -> None:
    draws = iter(["healthz", "METRICS", "Xy12abZ"])  # two reserved words, then a fine code
    monkeypatch.setattr(shortcode, "_random_code", lambda: next(draws))
    assert generate_code() == "Xy12abZ"


def test_random_code_gives_up_if_only_reserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shortcode, "_random_code", lambda: "healthz")
    with pytest.raises(shortcode.CodeGenerationError):
        generate_code()
