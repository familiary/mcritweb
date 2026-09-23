"""The one place a type-ahead's suggestion data is built.

`static/autocomplete.js` is vendored and may not be patched. It renders every
suggestion through `innerHTML` and interpolates the same value into a double-quoted
`data-label` / `data-value` attribute, so a family name is markup there, not text -
and a family name is chosen by whoever submits or renames a family. The escaping
therefore happens on the way in, and the widget only ever sees escaped names (#168).

This lives in its own module because there are now two ways in, and they must not
drift: the `autocomplete_items` template filter, for names rendered into a page, and
`explore.family_names`, which answers the same names as JSON for the type-ahead that
fetches them as they are typed (#146). `|tojson` protects the transport in the first
case and `jsonify` in the second; neither is escaping, and the sink is one `innerHTML`
further on in both.

Known cost, since it is not free: the widget slices the escaped label, so a lookup
landing inside an entity renders it literally - "R&amp;D" for a family named "R&D" -
and a lookup containing one of the five escaped characters no longer substring-matches.
`item.label` is the one value the matcher, the highlighter and the attribute all read,
so there is no field to point at raw text for matching and escaped text for rendering.
tests/testAutocompleteEscaping.py pins this rather than leaving it to a comment.

`escape` stringifies, so a non-string name arrives as e.g. "None". Family names are
always strings off the backend, and the code this replaced handed `null` to
removeDiacritics(), which threw and took the whole type-ahead with it.
"""

from markupsafe import escape


def autocomplete_items(names):
    """`[{label, value}]` for Autocomplete, with both fields HTML-escaped."""
    return [{'label': str(escape(name)), 'value': str(escape(name))} for name in names]
