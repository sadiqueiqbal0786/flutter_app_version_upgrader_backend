"""Semantic version parsing and ordering. Mirrors the Dart implementation in
the plugin so both sides agree on precedence."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import total_ordering

_NUMERIC = re.compile(r"^[0-9]+$")
_IDENT = re.compile(r"^[0-9A-Za-z-]+$")


class VersionFormatError(ValueError):
    pass


@total_ordering
@dataclass(frozen=True)
class SemVer:
    major: int
    minor: int
    patch: int
    pre_release: tuple[str, ...] = field(default_factory=tuple)
    build_meta: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def parse(cls, text: str | None) -> "SemVer":
        v = cls.try_parse(text)
        if v is None:
            raise VersionFormatError(f"invalid version {text!r}")
        return v

    @classmethod
    def try_parse(cls, text: str | None) -> "SemVer | None":
        if text is None:
            return None
        s = text.strip()
        if not s:
            return None
        if s[0] in "vV":
            s = s[1:]
        build: tuple[str, ...] = ()
        if "+" in s:
            s, meta = s.split("+", 1)
            if not meta:
                return None
            build = tuple(meta.split("."))
            if any(not _IDENT.match(b) for b in build):
                return None
        pre: tuple[str, ...] = ()
        if "-" in s:
            s, p = s.split("-", 1)
            if not p:
                return None
            pre = tuple(p.split("."))
            if any(not _IDENT.match(x) for x in pre):
                return None
        parts = s.split(".")
        if not 1 <= len(parts) <= 3:
            return None
        nums: list[int] = []
        for part in parts:
            if not _NUMERIC.match(part):
                return None
            nums.append(int(part))
        while len(nums) < 3:
            nums.append(0)
        return cls(nums[0], nums[1], nums[2], pre, build)

    @property
    def is_pre_release(self) -> bool:
        return bool(self.pre_release)

    def _key(self) -> tuple:
        return (self.major, self.minor, self.patch)

    @staticmethod
    def _cmp_pre(a: tuple[str, ...], b: tuple[str, ...]) -> int:
        if not a and not b:
            return 0
        if not a:
            return 1
        if not b:
            return -1
        for x, y in zip(a, b):
            xn = int(x) if _NUMERIC.match(x) else None
            yn = int(y) if _NUMERIC.match(y) else None
            if xn is not None and yn is not None:
                if xn != yn:
                    return -1 if xn < yn else 1
            elif xn is not None:
                return -1
            elif yn is not None:
                return 1
            elif x != y:
                return -1 if x < y else 1
        return (len(a) > len(b)) - (len(a) < len(b))

    def compare(self, other: "SemVer") -> int:
        if self._key() != other._key():
            return -1 if self._key() < other._key() else 1
        return self._cmp_pre(self.pre_release, other.pre_release)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SemVer) and self.compare(other) == 0

    def __lt__(self, other: "SemVer") -> bool:
        return self.compare(other) < 0

    def __hash__(self) -> int:
        return hash((self.major, self.minor, self.patch, self.pre_release))

    def __str__(self) -> str:
        s = f"{self.major}.{self.minor}.{self.patch}"
        if self.pre_release:
            s += "-" + ".".join(self.pre_release)
        if self.build_meta:
            s += "+" + ".".join(self.build_meta)
        return s


def release_sort_key(version: str, build: int | None) -> tuple[SemVer, int]:
    """Orders releases by semantic version, then build number."""
    return (SemVer.parse(version), build if build is not None else -1)


def compare_release(a_version: str, a_build: int | None, b_version: str, b_build: int | None) -> int:
    c = SemVer.parse(a_version).compare(SemVer.parse(b_version))
    if c != 0:
        return c
    if a_build is None or b_build is None:
        return 0
    return (a_build > b_build) - (a_build < b_build)
