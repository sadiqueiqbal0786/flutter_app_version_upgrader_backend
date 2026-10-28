import pytest

from app.versioning import SemVer, VersionFormatError, compare_release


def test_parse_and_defaults():
    assert SemVer.parse("2.7.0") == SemVer(2, 7, 0)
    assert SemVer.parse("2.7") == SemVer(2, 7, 0)
    assert SemVer.parse(" v3 ") == SemVer(3, 0, 0)
    v = SemVer.parse("2.8.0-beta.2+45")
    assert v.pre_release == ("beta", "2") and v.build_meta == ("45",)
    assert str(v) == "2.8.0-beta.2+45"


@pytest.mark.parametrize("bad", ["", "abc", "1.2.3.4", "1..2", "1.2.3-", "1.2.3+", None])
def test_rejects_garbage(bad):
    assert SemVer.try_parse(bad) is None
    with pytest.raises(VersionFormatError):
        SemVer.parse(bad)


def test_numeric_not_lexical():
    assert SemVer.parse("2.10.0") > SemVer.parse("2.9.0")
    assert "2.10.0" < "2.9.0"  # the string comparison we must not use


def test_semver_precedence_chain():
    chain = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]
    for a, b in zip(chain, chain[1:]):
        assert SemVer.parse(a) < SemVer.parse(b), f"{a} < {b}"


def test_build_metadata_ignored():
    assert SemVer.parse("1.0.0+1") == SemVer.parse("1.0.0+2")


def test_compare_release_uses_build_as_tiebreak():
    assert compare_release("2.5.0", 250, "2.5.0", 251) < 0
    assert compare_release("2.5.0", None, "2.5.0", 251) == 0
    assert compare_release("2.6.0", 1, "2.5.0", 999) > 0
