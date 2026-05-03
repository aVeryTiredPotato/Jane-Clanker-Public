from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable


BYPASS_PATTERNS = {
    "a": "(a|@|4|Ã |Ã¡|Ã¢|Ã£|Ã¤|Ã¥|Ä|Äƒ|Ä…|Ã¦)",
    "b": "(b|8|ÃŸ|13|!3)",
    "c": "(c|\\(|Ã§|Ä‡|Ä‰|Ä‹|Ä)",
    "d": "(d|Ä|Ä‘)",
    "e": "(e|Æ|3|Î£|Ã¨|Ã©|Ãª|Ã«|Ä“|Ä•|Ä—|Ä™|Ä›)",
    "f": "(f|Æ’|ph)",
    "g": "(g|q|ð”¾|â‚²|6|9|Ä|ÄŸ|Ä¡|Ä£)",
    "h": "(h|Ä¥|Ä§|#)",
    "i": "(i|1|!|\\||Ã¬|Ã­|Ã®|Ã¯|Ä«|Ä­|Ä¯|Ä±|l|ã€)",
    "j": "(j|Äµ)",
    "k": "(k|Ä·|Ä¸|\\|<|\\|{|\\|c|/\\|)",
    "l": "(l|1|!|\\||Äº|Ä¼|Ä¾|Å€|Å‚|i)",
    "m": "(m|/\\/\\|\\^\\^|nn)",
    "n": "(n|Î·|Ã±|Å„|Å†|Åˆ|Å‰|Å‹|á‘Ž)",
    "o": "(o|0|Â°|Ã¸|Ã¶|Ã³|Ã²|Ã´|Ãµ|Å|Å|Å‘|Å“)",
    "p": "(p|Ã¾|Ï|q)",
    "q": "(q|9|g)",
    "r": "(r|Å•|Å—|Å™|Ð¯)",
    "s": "(s|\\$|5|Â§|Å›|Å|ÅŸ|Å¡|z)",
    "t": "(t|7|\\+|â€ |Å£|Å¥|Å§)",
    "u": "(u|Âµ|Ï…|Ã¼|Ãº|Ã¹|Ã»|Å«|Å­|Å¯|Å±|Å³)",
    "v": "(v|\\/)",
    "w": "(w|Åµ|vv|\\/\\/)",
    "x": "(x|Ã—|Ï‡|><)",
    "y": "(y|Â¥|Ã¿|Ã½|Å·|È³|j)",
    "z": "(z|2|Å¼|Åº|Å¾|s)",
}

ALT_ACCOUNT_WORDS = (
    "alt",
    "alts",
    "backup",
    "backups",
    "back_up",
    "bak",
    "bckup",
    "spare",
    "second",
    "secondaccount",
    "account",
    "acct",
    "acc",
    "clone",
    "copy",
    "new",
    "old",
    "main",
    "again",
)

_USERNAME_KEY_RE = re.compile(r"[^a-z0-9]+", re.IGNORECASE)
_SEPARATOR_PATTERN = r"(?:[\s\W_]*?)"
_EMPTY_MATCH_RE = re.compile(r"a\A")


def normalized_username_key(value: str) -> str:
    return _USERNAME_KEY_RE.sub("", str(value or "").strip().lower())


def _normalized_words(words: Iterable[str] | None) -> tuple[str, ...]:
    rawWords = tuple(words or ALT_ACCOUNT_WORDS)
    normalized = []
    seen: set[str] = set()
    for word in rawWords:
        key = normalized_username_key(str(word or ""))
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    return tuple(normalized)


def generate_regex_pattern(word: str) -> str:
    key = normalized_username_key(word)
    pattern = ""
    for index, character in enumerate(key):
        charPattern = BYPASS_PATTERNS.get(character.lower(), re.escape(character))
        pattern += f"(?:{charPattern})"
        if index != len(key) - 1:
            pattern += _SEPARATOR_PATTERN
    return pattern


@lru_cache(maxsize=2048)
def _build_username_variant_regex_cached(usernameKey: str, altWords: tuple[str, ...]) -> re.Pattern[str]:
    if len(usernameKey) < 3:
        return _EMPTY_MATCH_RE

    core = generate_regex_pattern(usernameKey)
    choices: list[str] = []

    if len(usernameKey) >= 5:
        choices.append(core)

    altPatterns = [
        generate_regex_pattern(word)
        for word in altWords
        if len(word) >= 2
    ]
    if altPatterns:
        marker = "(?:" + "|".join(altPatterns) + ")"
        optionalDigits = r"(?:\d{1,4})?"
        choices.extend(
            [
                f"{marker}{_SEPARATOR_PATTERN}{core}{_SEPARATOR_PATTERN}{optionalDigits}",
                f"{core}{_SEPARATOR_PATTERN}{marker}{_SEPARATOR_PATTERN}{optionalDigits}",
                f"{marker}{_SEPARATOR_PATTERN}{optionalDigits}{_SEPARATOR_PATTERN}{core}",
                f"{core}{_SEPARATOR_PATTERN}{optionalDigits}{_SEPARATOR_PATTERN}{marker}",
            ]
        )

    if len(usernameKey) >= 5:
        choices.append(f"{core}{_SEPARATOR_PATTERN}\\d{{1,4}}")

    if not choices:
        return _EMPTY_MATCH_RE
    return re.compile("^(?:" + "|".join(choices) + ")$", re.IGNORECASE)


def build_username_variant_regex(
    username: str,
    *,
    altWords: Iterable[str] | None = None,
) -> re.Pattern[str]:
    return _build_username_variant_regex_cached(
        normalized_username_key(username),
        _normalized_words(altWords),
    )


def username_alt_match_reason(
    candidate: str,
    knownUsername: str,
    *,
    altWords: Iterable[str] | None = None,
) -> str | None:
    candidateText = str(candidate or "").strip()
    knownText = str(knownUsername or "").strip()
    candidateKey = normalized_username_key(candidateText)
    knownKey = normalized_username_key(knownText)
    if len(candidateKey) < 3 or len(knownKey) < 3:
        return None
    if candidateText.lower() == knownText.lower():
        return None

    pattern = build_username_variant_regex(knownKey, altWords=altWords)
    if not pattern.fullmatch(candidateText):
        return None

    markerWords = _normalized_words(altWords)
    markerFound = any(word and word in candidateKey and word not in knownKey for word in markerWords)
    if markerFound:
        return "known member username with an alt/back-up marker"
    if candidateKey == knownKey:
        return "known member username with separator or case changes"
    return "known member username with alternate characters"


def looks_like_username_alt(
    candidate: str,
    knownUsername: str,
    *,
    altWords: Iterable[str] | None = None,
) -> bool:
    return username_alt_match_reason(candidate, knownUsername, altWords=altWords) is not None


def build_trigger_regex(triggerWords: dict[str, Iterable[str]] | None = None) -> re.Pattern[str]:
    patterns: list[str] = []
    for baseWord, variations in (triggerWords or {}).items():
        patterns.append(generate_regex_pattern(baseWord))
        for variation in variations:
            patterns.append(generate_regex_pattern(variation))
    if not patterns:
        return _EMPTY_MATCH_RE
    return re.compile("|".join(patterns), re.IGNORECASE)
