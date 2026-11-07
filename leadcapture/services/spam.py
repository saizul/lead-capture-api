"""Heuristic spam scoring. Cheap, explainable, and tunable per form."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

LINK_RE = re.compile(r"(?i)(?:https?://|www\.)[^\s<>\"']+|<a\s+href")

DEFAULT_BLOCKED_WORDS = frozenset(
    {
        "viagra",
        "cialis",
        "casino",
        "porn",
        "xxx",
        "payday loan",
        "crypto giveaway",
        "forex signals",
        "seo services",
        "backlinks",
        "buy followers",
        "weight loss pills",
        "work from home",
        "make money fast",
        "bitcoin investment",
        "free money",
        "click here",
        "whatsapp me",
        "telegram me",
        "guaranteed ranking",
    }
)

DISPOSABLE_DOMAINS = frozenset(
    {
        "mailinator.com",
        "guerrillamail.com",
        "guerrillamail.net",
        "guerrillamail.org",
        "10minutemail.com",
        "10minutemail.net",
        "temp-mail.org",
        "tempmail.com",
        "tempmail.net",
        "throwawaymail.com",
        "yopmail.com",
        "yopmail.net",
        "trashmail.com",
        "trashmail.net",
        "getnada.com",
        "dispostable.com",
        "maildrop.cc",
        "sharklasers.com",
        "grr.la",
        "fakeinbox.com",
        "mintemail.com",
        "mohmal.com",
        "emailondeck.com",
        "spamgourmet.com",
        "mailnesia.com",
        "moakt.com",
        "tmpmail.org",
        "tmpmail.net",
        "burnermail.io",
        "discard.email",
        "mytemp.email",
        "inboxkitten.com",
        "tempail.com",
        "minuteinbox.com",
        "emailfake.com",
        "tempinbox.com",
        "spambox.us",
        "mailcatch.com",
        "anonbox.net",
        "33mail.com",
    }
)

SCORE_ONE_LINK = 10
SCORE_TWO_LINKS = 25
SCORE_MANY_LINKS = 50
SCORE_BLOCKED_WORD = 30
SCORE_BLOCKED_WORD_CAP = 90
SCORE_DISPOSABLE_EMAIL = 40


@dataclass
class SpamResult:
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    threshold: int = 50

    @property
    def is_spam(self) -> bool:
        return self.score >= self.threshold


def count_links(text: str) -> int:
    return len(LINK_RE.findall(text))


def find_blocked_words(text: str, words: Iterable[str]) -> list[str]:
    lowered = text.lower()
    hits = []
    for word in sorted({w.strip().lower() for w in words if w.strip()}):
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", lowered):
            hits.append(word)
    return hits


def is_disposable_email(email: str | None) -> bool:
    if not email or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1].lower()
    return domain in DISPOSABLE_DOMAINS or any(domain.endswith("." + d) for d in DISPOSABLE_DOMAINS)


def analyze(
    texts: Iterable[str],
    email: str | None = None,
    *,
    extra_blocked_words: Iterable[str] = (),
    threshold: int = 50,
) -> SpamResult:
    """Score the free-text values of a submission."""
    combined = "\n".join(texts)
    result = SpamResult(threshold=threshold)

    links = count_links(combined)
    if links >= 3:
        result.score += SCORE_MANY_LINKS
        result.reasons.append(f"links:{links}")
    elif links == 2:
        result.score += SCORE_TWO_LINKS
        result.reasons.append("links:2")
    elif links == 1:
        result.score += SCORE_ONE_LINK
        result.reasons.append("links:1")

    hits = find_blocked_words(combined, [*DEFAULT_BLOCKED_WORDS, *extra_blocked_words])
    if hits:
        result.score += min(len(hits) * SCORE_BLOCKED_WORD, SCORE_BLOCKED_WORD_CAP)
        result.reasons.extend(f"blocked_word:{w}" for w in hits)

    if is_disposable_email(email):
        result.score += SCORE_DISPOSABLE_EMAIL
        result.reasons.append("disposable_email")

    return result
