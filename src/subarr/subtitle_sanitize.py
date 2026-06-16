"""#216 Phase 2: sanitize untrusted external subtitle text for UI display.

The existing-subtitle audit renders cue text from provider/scene SRTs the user
did NOT generate. A crafted .srt/.ass can carry HTML/script or ASS override
(including \\t transforms, \\clip, and drawing) tags — naively injecting that
into the DOM is a stored-XSS vector. (Our own Whisper output is clean; this is
specifically for arbitrary third-party subs.)

`sanitize_cue_text` reduces a cue to inert, display-only text: active elements
(script/style) are dropped whole, all tag delimiters and ASS override blocks
are stripped so nothing structural can reach the DOM, control characters are
removed, and whitespace is normalized. React escapes by default — this is the
belt to that suspenders, and it also strips ASS noise React would happily
render as literal clutter. Sanitize at this boundary so the API never emits
raw untrusted markup.
"""

from __future__ import annotations

import re

# Elements whose CONTENT is never display text — drop tag + body together.
_ACTIVE_ELEMENT = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
# ASS/SSA override blocks: {\an8}, {\pos(..)}, {\t(..)}, {\clip(..)}, drawing, etc.
_ASS_OVERRIDE = re.compile(r"\{[^}]*\}")
# ASS soft line break (\N, \n) and hard space (\h).
_ASS_WHITESPACE = re.compile(r"\\[Nnh]")
# Any remaining tag-like construct (<b>, <font ...>, stray '<', '>').
_TAGS = re.compile(r"<[^>]*>")
_STRAY_ANGLES = re.compile(r"[<>]")
# C0/C1 control characters (keep \t/\n/\r handled separately via whitespace).
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_WHITESPACE = re.compile(r"\s+")


def sanitize_cue_text(raw: str | None) -> str:
    """Return inert, display-only text for an untrusted subtitle cue."""
    if not raw:
        return ""
    text = str(raw)
    text = _ACTIVE_ELEMENT.sub(" ", text)  # drop <script>/<style> wholesale
    text = _ASS_OVERRIDE.sub(" ", text)  # {\...} override blocks
    text = _ASS_WHITESPACE.sub(" ", text)  # \N \n \h -> space
    text = _TAGS.sub(" ", text)  # remaining <...> tags
    text = _STRAY_ANGLES.sub(" ", text)  # any leftover bare < or >
    text = _CONTROL.sub("", text)  # control chars
    text = _WHITESPACE.sub(" ", text)  # collapse runs
    return text.strip()
