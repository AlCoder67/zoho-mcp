"""Golden-output regression gate for ``_plaintext_to_safe_html``.

Why this file exists, separate from the ordinary unit tests in
``test_client_http.py``: two real bugs shipped to live sends in the same
afternoon (2026-09-14) because the unit tests exercised short, synthetic
snippets ("Line one\\nLine two") that happened not to trigger either
failure mode. Neither bug was subtle in the actual output -- both were
visible in a screenshot within seconds -- but nothing in the test suite
would have caught either before a human had to notice a wrong-looking
live email:

1. Splitting only on blank lines ("\\n\\n") collapsed an entire real
   draft into one ``<p>`` with no visible spacing, because real Monarc
   drafts never contain a blank line (verified across all 12 real drafts
   sent that day). A short two-line test snippet has nothing to collapse
   into, so it passed while production was broken.
2. The sign-off ("Name | Company" then a bare domain on the next line)
   rendered as two separate paragraphs with a visible gap, instead of
   one signature block. No prior test used real signature-block content.

**The fix for "this class of bug should never reach a live send again"
is this file, not more code in the function itself.** Each fixture below
is either lifted verbatim from a real Monarc draft that was independently
confirmed correct (via raw MIME inspection of the actual sent message --
see the comment on each fixture) or is a deliberately adversarial variant
designed to probe an edge the real fixtures don't cover. Every fixture's
expected output is asserted exactly (not just "contains <br>" or "count
of <p> looks right") -- a golden-string comparison is the only shape that
fails loudly the moment *any* future change alters real rendered output,
intentionally or not, which is exactly the property a live send needs
and a narrower assertion doesn't give you.

**When a new live rendering bug is found:** the fix belongs here as a
new fixture using the actual real content that broke, confirmed correct
by the same live-MIME-verification discipline as the existing ones --
not just a shorter reproduction of the bug. A fixture that isn't drawn
from something that was actually sent (or would plausibly be sent) is
weaker evidence than one that is.
"""

from zoho_mcp.zoho.client import _plaintext_to_safe_html

# --------------------------------------------------------------------------
# Fixture 1: the real Nutrire Day-1 outreach draft, sent live 2026-09-14
# (Zoho message id 1789387431829138300) after both the paragraph-collapse
# fix and the signature-block fix. This exact input/output pair was
# confirmed correct by fetching the message's raw MIME source
# (get_email_source, include_raw=true) and checking the text/html part
# for real per-line <p> markup with the "Ofentse Shuping | Monarc
# Media<br>monarcmediahq.com" pairing -- not just re-running this
# function and trusting its own output.
# --------------------------------------------------------------------------

NUTRIRE_DRAFT_INPUT = """Hi Jessica,
Nutrire just landed its first-ever retail door — a nationwide launch with Ulta Beauty, part of Ulta's "Sparked" early-stage program, in-store since 8/23. First retail placement is a real inflection point for a brand this young.
The shelf-to-TikTok discovery window is open right now. A shopper who finds Nutrire on an Ulta shelf checks TikTok within 72 hours — what they find there decides whether they convert.
Haircare is one of the best formats for demo content that closes that gap — a single well-placed video can do the work of a shelf display, showing exactly what a shopper would otherwise have to guess at.
Wrote two quick scripts for the Ulta launch lineup — attached below. No ask, just showing the thinking.
Ofentse Shuping | Monarc Media
monarcmediahq.com
---
SCRIPT 1: "First time on a shelf"
Hook (0-3s): "This brand's never been in a store before. Now it's at Ulta."
Middle (3-15s): Creator picks the product off an Ulta shelf, talks about it being Nutrire's first retail door, does a quick application/demo.
CTA (last 3s): "At Ulta now, nationwide."
Format: TikTok organic
Tone: discovery, milestone-forward
SCRIPT 2: "Salon-incubated, now on shelf"
Hook (0-3s): "This was built inside an actual salon before it ever hit a store."
Middle (3-15s): Creator explains the Tricoci Salon & Spa incubation origin, demos the product, talks through what that background means for the formulation.
CTA (last 3s): "Find it at Ulta."
Format: Meta feed
Tone: credibility-led, direct"""

NUTRIRE_DRAFT_EXPECTED_HTML = (
    "<p>Hi Jessica,</p>"
    "<p>Nutrire just landed its first-ever retail door — a nationwide launch"
    " with Ulta Beauty, part of Ulta&#x27;s &quot;Sparked&quot; early-stage"
    " program, in-store since 8/23. First retail placement is a real"
    " inflection point for a brand this young.</p>"
    "<p>The shelf-to-TikTok discovery window is open right now. A shopper"
    " who finds Nutrire on an Ulta shelf checks TikTok within 72 hours —"
    " what they find there decides whether they convert.</p>"
    "<p>Haircare is one of the best formats for demo content that closes"
    " that gap — a single well-placed video can do the work of a shelf"
    " display, showing exactly what a shopper would otherwise have to"
    " guess at.</p>"
    "<p>Wrote two quick scripts for the Ulta launch lineup — attached"
    " below. No ask, just showing the thinking.</p>"
    "<p>Ofentse Shuping | Monarc Media<br>monarcmediahq.com</p>"
    "<p>---</p>"
    '<p>SCRIPT 1: &quot;First time on a shelf&quot;</p>'
    "<p>Hook (0-3s): &quot;This brand&#x27;s never been in a store before."
    " Now it&#x27;s at Ulta.&quot;</p>"
    "<p>Middle (3-15s): Creator picks the product off an Ulta shelf, talks"
    " about it being Nutrire&#x27;s first retail door, does a quick"
    " application/demo.</p>"
    '<p>CTA (last 3s): &quot;At Ulta now, nationwide.&quot;</p>'
    "<p>Format: TikTok organic</p>"
    "<p>Tone: discovery, milestone-forward</p>"
    '<p>SCRIPT 2: &quot;Salon-incubated, now on shelf&quot;</p>'
    "<p>Hook (0-3s): &quot;This was built inside an actual salon before it"
    " ever hit a store.&quot;</p>"
    "<p>Middle (3-15s): Creator explains the Tricoci Salon &amp; Spa"
    " incubation origin, demos the product, talks through what that"
    " background means for the formulation.</p>"
    '<p>CTA (last 3s): &quot;Find it at Ulta.&quot;</p>'
    "<p>Format: Meta feed</p>"
    "<p>Tone: credibility-led, direct</p>"
)


def test_nutrire_draft_matches_live_verified_output():
    """The exact real draft sent live, confirmed correct via raw MIME.

    If this fails, real rendering has changed -- go verify the new output
    against a live send (raw MIME, not just this function's own opinion
    of itself) before updating the golden string, per this module's
    docstring.
    """
    assert _plaintext_to_safe_html(NUTRIRE_DRAFT_INPUT) == NUTRIRE_DRAFT_EXPECTED_HTML


def test_nutrire_draft_has_no_empty_or_run_on_paragraphs():
    """Structural cross-check independent of the golden string above.

    Guards specifically against the 2026-09-14 collapsed-paragraph
    regression re-appearing in a form that happens to slip past a stale
    golden string (e.g. someone updates the constant above to match a new
    bug instead of catching it) -- every line of real content should be
    its own <p>, one per line of the source, no more, no fewer.
    """
    html = _plaintext_to_safe_html(NUTRIRE_DRAFT_INPUT)
    nonblank_lines = [line for line in NUTRIRE_DRAFT_INPUT.split("\n") if line.strip()]
    # The signature pair (2 lines) collapses into 1 <p>, so the paragraph
    # count is exactly one less than the nonblank line count for any
    # fixture containing exactly one signature pair.
    assert html.count("<p>") == len(nonblank_lines) - 1
    assert html.count("<br>") == 1
    assert "<p></p>" not in html


# --------------------------------------------------------------------------
# Fixture 2: the real Rafael Febres reply draft, sent live 2026-09-14
# (Zoho message id, reply_draft path rather than create_draft -- exercises
# the exact same helper, confirmed correct the same way).
# --------------------------------------------------------------------------

RAFAEL_REPLY_INPUT = """Hi Rafael,
Thanks for reaching out. Travel and tech/apps are real categories we work in, so there's a plausible fit here.
Before going further, could you send a couple of example videos directly — actual finished UGC, not just a portfolio link? Specifically anything in the tech/app or travel space would be most useful to see.
Once I've had a look, I'll follow up on next steps.
Ofentse Shuping | Monarc Media
monarcmediahq.com"""

RAFAEL_REPLY_EXPECTED_HTML = (
    "<p>Hi Rafael,</p>"
    "<p>Thanks for reaching out. Travel and tech/apps are real categories"
    " we work in, so there&#x27;s a plausible fit here.</p>"
    "<p>Before going further, could you send a couple of example videos"
    " directly — actual finished UGC, not just a portfolio link?"
    " Specifically anything in the tech/app or travel space would be most"
    " useful to see.</p>"
    "<p>Once I&#x27;ve had a look, I&#x27;ll follow up on next steps.</p>"
    "<p>Ofentse Shuping | Monarc Media<br>monarcmediahq.com</p>"
)


def test_rafael_reply_matches_live_verified_output():
    """Real reply-draft content, confirmed correct via raw MIME on send."""
    assert _plaintext_to_safe_html(RAFAEL_REPLY_INPUT) == RAFAEL_REPLY_EXPECTED_HTML


# --------------------------------------------------------------------------
# Adversarial / edge-case fixtures -- not drawn from a real send, but
# probing shapes the two real fixtures above don't exercise.
# --------------------------------------------------------------------------


def test_message_with_no_signature_block_gets_no_br():
    """A message that never contains the "Name | Co" + domain pattern
    should never accidentally trigger the signature-pairing logic --
    every line stays its own <p>, zero <br> tags anywhere.
    """
    content = "Point one.\nPoint two.\nPoint three, no sign-off here."
    html = _plaintext_to_safe_html(content)
    assert html == "<p>Point one.</p><p>Point two.</p><p>Point three, no sign-off here.</p>"
    assert "<br>" not in html


def test_pipe_character_without_following_domain_is_not_paired():
    """A line containing "|" for an unrelated reason (e.g. a script format
    description) must not pair with whatever line happens to follow it,
    unless that next line is actually a bare-domain shape.
    """
    content = "Format: TikTok | Meta feed\nTone: casual, direct"
    html = _plaintext_to_safe_html(content)
    assert html == "<p>Format: TikTok | Meta feed</p><p>Tone: casual, direct</p>"
    assert "<br>" not in html


def test_signature_pattern_at_very_end_of_message_still_pairs():
    """The signature pair-detection must work when it's the last two
    lines with nothing after them (the common case -- every real Monarc
    draft ends exactly this way), not just mid-message.
    """
    content = "Body.\nOfentse Shuping | Monarc Media\nmonarcmediahq.com"
    html = _plaintext_to_safe_html(content)
    assert html == "<p>Body.</p><p>Ofentse Shuping | Monarc Media<br>monarcmediahq.com</p>"


def test_signature_pattern_not_at_end_still_pairs_and_resumes_normally():
    """Confirms the pairing consumes exactly two lines and continues
    normal one-line-per-<p> handling for whatever follows -- this is what
    every real Monarc draft actually looks like (signature mid-message,
    followed by a "---" separator and script content).
    """
    content = "Body.\nOfentse Shuping | Monarc Media\nmonarcmediahq.com\nMore content after."
    html = _plaintext_to_safe_html(content)
    assert html == (
        "<p>Body.</p>"
        "<p>Ofentse Shuping | Monarc Media<br>monarcmediahq.com</p>"
        "<p>More content after.</p>"
    )


def test_domain_look_alike_with_a_space_is_not_treated_as_a_domain():
    """The bare-domain match must require no whitespace -- a line with a
    "|" followed by an ordinary sentence that happens to contain a dot
    (e.g. "e.g." or a decimal) must not falsely pair.
    """
    content = "Name | Team\nThis costs $19.99 total, not a domain."
    html = _plaintext_to_safe_html(content)
    assert html == (
        "<p>Name | Team</p>"
        "<p>This costs $19.99 total, not a domain.</p>"
    )
    assert "<br>" not in html


def test_blank_lines_never_produce_empty_paragraphs():
    """Even though real Monarc drafts never contain blank lines, a blank
    line must never survive as a stray ``<p></p>`` if one ever does
    appear (e.g. hand-edited content, or a future caller with different
    authoring habits).
    """
    content = "Line one.\n\n\nLine two."
    html = _plaintext_to_safe_html(content)
    assert "<p></p>" not in html
    assert html == "<p>Line one.</p><p>Line two.</p>"


def test_html_special_characters_are_always_escaped():
    """Regardless of paragraph/signature handling, no caller-controlled
    "<", ">", "&", quote, or apostrophe may ever survive as live markup --
    this is the actual XSS/injection guard for content that can originate
    from an untrusted quoted email (reply_draft's real threat model).
    """
    content = "<script>alert(1)</script>\nA & B\n\"quoted\" and 'single'"
    html = _plaintext_to_safe_html(content)
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "A &amp; B" in html
    assert "&quot;quoted&quot;" in html
    assert "&#x27;single&#x27;" in html
