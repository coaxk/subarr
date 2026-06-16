"""#216 Phase 2: sanitize UNTRUSTED external subtitle text before it can reach
the UI (stored-XSS gate). The existing-audit feature renders cue text from
provider/scene SRTs the user did not generate — a crafted .srt/.ass can carry
HTML/script or ASS override/Lua/drawing tags. These pin that the sanitizer
yields inert, display-only text.
"""

from __future__ import annotations

from subarr.subtitle_sanitize import sanitize_cue_text


class TestStripsActiveContent:
    def test_script_element_removed_wholesale(self):
        out = sanitize_cue_text("<script>alert('xss')</script>Hello")
        assert "alert" not in out
        assert "<script" not in out
        assert out.strip() == "Hello"

    def test_style_element_removed_wholesale(self):
        out = sanitize_cue_text("<style>body{display:none}</style>Subtitle")
        assert "display:none" not in out
        assert out.strip() == "Subtitle"

    def test_img_onerror_payload_neutralized(self):
        out = sanitize_cue_text('<img src=x onerror="alert(1)">caption')
        assert "<img" not in out
        assert "onerror" not in out
        assert "caption" in out

    def test_no_angle_brackets_survive(self):
        # whatever residual text remains, no tag delimiters can reach the DOM
        out = sanitize_cue_text("<b>bold</b> and <i>italic</i>")
        assert "<" not in out and ">" not in out
        assert "bold" in out and "italic" in out


class TestStripsAssOverrideTags:
    def test_position_and_alignment_override_removed(self):
        assert sanitize_cue_text("{\\an8}{\\pos(100,200)}Top line").strip() == "Top line"

    def test_lua_style_transform_override_removed(self):
        out = sanitize_cue_text("{\\t(\\fscx150)}{\\clip(0,0,1,1)}danger")
        assert "{" not in out and "}" not in out
        assert "danger" in out

    def test_ass_linebreaks_become_spaces(self):
        assert sanitize_cue_text("line one\\Nline two\\hgap").strip() == "line one line two gap"


class TestPreservesLegitimateText:
    def test_plain_text_unchanged(self):
        assert sanitize_cue_text("Hello, world!") == "Hello, world!"

    def test_apostrophes_and_punctuation_kept(self):
        assert sanitize_cue_text("It's a trap — really?") == "It's a trap — really?"

    def test_control_chars_removed_but_text_kept(self):
        assert sanitize_cue_text("clean\x00\x07text") == "cleantext"

    def test_whitespace_collapsed_and_trimmed(self):
        assert sanitize_cue_text("  too    many   spaces  ") == "too many spaces"

    def test_empty_and_none_safe(self):
        assert sanitize_cue_text("") == ""
        assert sanitize_cue_text(None) == ""


def test_realistic_scene_ad_with_markup():
    raw = '<font color="#ff0000">{\\an8}Downloaded from \t<a href="http://spam">OpenSubtitles</a></font>'
    out = sanitize_cue_text(raw)
    assert "<" not in out and ">" not in out and "{" not in out
    assert "Downloaded from" in out
    assert "OpenSubtitles" in out
