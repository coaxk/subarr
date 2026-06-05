# Flag assets

SVG flags (4x3) from [flag-icons](https://github.com/lipis/flag-icons) by Panayiotis Lipiridis, **MIT licensed**.

Used purely as **decoration** in language tags (`LangTag` in `home-hifi/atoms.jsx`).
A flag is not a geography claim — languages are not countries; the tag's tooltip
always names the *language*, and multi-country languages (en, es, ar, pt) carry no
flag (just the code). We bundle these locally (rather than use emoji) because
Windows ships no flag glyphs in its emoji font, and serve them from subarr's own
static mount so there are no external requests.

Only the subset of countries referenced by `LANG_INFO[*].cc` is included.
