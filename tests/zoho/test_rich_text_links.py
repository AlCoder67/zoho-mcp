"""Allowlisted hyperlink markup in the rich-text converter (owner instruction 2026-10-01).

``[case study](URL)`` becomes a real anchor only for an exact allowlisted URL. Everything else must
stay inert text: a link allowlist that can be widened by the caller is not an allowlist.
"""
import pytest

from zoho_mcp.zoho.client import _ALLOWED_LINK_URLS, _plaintext_to_safe_html

URL = "https://www.monarcmediahq.com/case-study-haruharu.html"


def test_allowlist_is_exactly_the_case_study():
    assert _ALLOWED_LINK_URLS == frozenset({URL})


def test_allowlisted_markup_becomes_one_anchor():
    html = _plaintext_to_safe_html(f"The full [case study]({URL}) shows one video.")
    assert html == f'<p>The full <a href="{URL}">case study</a> shows one video.</p>'


def test_anchor_sits_inside_its_own_paragraph_with_neighbours_unchanged():
    html = _plaintext_to_safe_html(f"Hi Sam,\nSee the [case study]({URL}) today.\nBest")
    assert html == f'<p>Hi Sam,</p><p>See the <a href="{URL}">case study</a> today.</p><p>Best</p>'


@pytest.mark.parametrize("url", [
    "https://www.monarcmediahq.com/case-study-other.html",
    "https://monarcmediahq.com/case-study-haruharu.html",
    "http://www.monarcmediahq.com/case-study-haruharu.html",
    "https://evil.example/x",
    "javascript:alert(1)",
    "https://www.monarcmediahq.com/case-study-haruharu.html?x=1",
    "https://www.monarcmediahq.com/case-study-haruharu.html#top",
    "https://www.monarcmediahq.com/case-study-haruharu.htmlx",
])
def test_any_other_url_stays_literal_text(url):
    html = _plaintext_to_safe_html(f"See [case study]({url}) now.")
    assert "<a " not in html
    assert f"[case study]({url})" in html


def test_attribute_injection_attempts_stay_literal():
    for bad in (
        f'[case study]({URL}"onmouseover="x)',
        f"[case study]({URL}' onclick='x)",
        f"[case study]({URL}<script>)",
        f"[<b>case study</b>]({URL})",
        f"[case study\"x]({URL})",
    ):
        html = _plaintext_to_safe_html(bad)
        assert "<a " not in html, bad
        assert "<script" not in html and "<b>" not in html, bad


def test_link_text_cannot_carry_markup_or_be_empty_or_huge():
    assert "<a " not in _plaintext_to_safe_html(f"[]({URL})")
    assert "<a " not in _plaintext_to_safe_html(f"[{'x' * 80}]({URL})")
    assert "<a " not in _plaintext_to_safe_html(f"[a&b]({URL})")


def test_two_links_on_one_line_both_convert_and_a_bad_one_does_not():
    html = _plaintext_to_safe_html(f"[a]({URL}) and [b](https://evil.example/x) and [c]({URL})")
    assert html.count("<a ") == 2
    assert "[b](https://evil.example/x)" in html


def test_plain_text_without_markup_is_byte_identical_to_before():
    content = "Hi Sam,\nA line with [brackets] and (parens) and https://example.com.\nOfentse Shuping | Monarc Media\nmonarcmediahq.com"
    html = _plaintext_to_safe_html(content)
    assert "<a " not in html
    assert html == (
        "<p>Hi Sam,</p><p>A line with [brackets] and (parens) and https://example.com.</p>"
        "<p>Ofentse Shuping | Monarc Media<br>monarcmediahq.com</p>"
    )


def test_quotes_and_apostrophes_still_not_entity_escaped_next_to_a_link():
    html = _plaintext_to_safe_html(f'Liza\'s "case" [case study]({URL}) here')
    assert "&#x27;" not in html and "&quot;" not in html
    assert f'<a href="{URL}">case study</a>' in html


# --- Send-from-draft round trip (2026-10-01, mooncat): the link must stay inside its sentence ---
from zoho_mcp.zoho.client import _html_with_allowed_links_to_text  # noqa: E402


def test_draft_html_round_trips_with_the_link_inline_in_one_sentence():
    draft_html = (
        "<p>Hi Camilla,</p>"
        f'<p>The full <a href="{URL}">case study</a> shows one video we delivered.</p>'
        "<p>Ofentse Shuping | Monarc Media<br>monarcmediahq.com</p>"
    )
    text = _html_with_allowed_links_to_text(draft_html)
    assert f"The full [case study]({URL}) shows one video we delivered." in text.split("\n")
    assert _plaintext_to_safe_html(text) == (
        "<p>Hi Camilla,</p>"
        f'<p>The full <a href="{URL}">case study</a> shows one video we delivered.</p>'
        "<p>Ofentse Shuping | Monarc Media<br>monarcmediahq.com</p>"
    )


def test_round_trip_never_revives_a_non_allowlisted_anchor():
    text = _html_with_allowed_links_to_text('<p>See <a href="https://evil.example/x">this</a>.</p>')
    assert "[this](" not in text and "evil.example" not in text
    assert "<a" not in _plaintext_to_safe_html(text)
