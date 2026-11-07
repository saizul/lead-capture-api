from __future__ import annotations

from leadcapture.services.spam import (
    analyze,
    count_links,
    find_blocked_words,
    is_disposable_email,
)


def test_clean_message_scores_zero():
    result = analyze(["Hi, I would like to discuss a project."], "ada@example.com")
    assert result.score == 0 and not result.is_spam and result.reasons == []


def test_link_counting():
    assert count_links("see https://a.com and www.b.com and <a href='x'>") == 3
    assert count_links("no links here") == 0


def test_single_link_is_low_risk():
    result = analyze(["check https://example.com"])
    assert 0 < result.score < result.threshold


def test_many_links_is_spam():
    result = analyze(["https://a.com https://b.com https://c.com"])
    assert result.is_spam
    assert "links:3" in result.reasons


def test_blocked_word_hit():
    result = analyze(["Best CASINO bonus"])
    assert result.score == 30
    assert "blocked_word:casino" in result.reasons


def test_blocked_word_respects_word_boundaries():
    assert find_blocked_words("a classical casinos", ["casino"]) == []
    assert find_blocked_words("play casino now", ["casino"]) == ["casino"]


def test_multiword_blocked_phrase():
    assert "make money fast" in find_blocked_words("You can MAKE MONEY FAST!", ["make money fast"])


def test_blocked_words_capped():
    text = "viagra cialis casino porn xxx backlinks"
    assert analyze([text]).score == 90


def test_extra_blocked_words_per_form():
    assert analyze(["buy my widgets"], extra_blocked_words=["widgets"]).score == 30
    assert analyze(["buy my widgets"]).score == 0


def test_disposable_email_detected():
    assert is_disposable_email("x@mailinator.com")
    assert is_disposable_email("x@sub.mailinator.com")
    assert not is_disposable_email("x@gmail.com")
    assert not is_disposable_email(None)
    assert not is_disposable_email("not-an-email")


def test_disposable_email_adds_score():
    result = analyze(["hello"], "bot@yopmail.com")
    assert result.score == 40 and "disposable_email" in result.reasons


def test_signals_combine_and_cross_threshold():
    result = analyze(["casino https://a.com"], "x@mailinator.com")
    assert result.score == 10 + 30 + 40
    assert result.is_spam


def test_threshold_is_configurable():
    assert not analyze(["casino"], threshold=50).is_spam
    assert analyze(["casino"], threshold=30).is_spam
