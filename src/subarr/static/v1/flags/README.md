# Flag assets

Circular SVG flags from [circle-flags](https://github.com/HatScripts/circle-flags) by HatScripts, **MIT licensed**. Tiny (~1-3KB each) and built for small display — full-detail rectangular sets embed coats of arms that bloat to 100KB+ and are invisible at icon size.

Used purely as **decoration** in language tags (`LangTag` in `home-hifi/atoms.jsx`).
A flag is not a geography claim — languages are not countries; the tag's tooltip
always names the *language*, and multi-country languages (en, es, ar, pt) carry no
flag (just the code). We bundle these locally (rather than use emoji) because
Windows ships no flag glyphs in its emoji font, and serve them from subarr's own
static mount so there are no external requests.

Only the subset of countries referenced by `LANG_INFO[*].cc` is included.
