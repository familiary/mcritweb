# A dark theme on Bootstrap 5.0.2: named colours and a stylesheet the theme switches on

---
status: proposed — #70
---

Issue #70 asks for a dark mode, *"could be set in profile, so nothing has to be dynamically
switchable"*. This records how it is built, because the shape is not the obvious one and
every later colour change has to follow it.

## What stood in the way

- The vendored Bootstrap is 5.0.2. Colour modes (`data-bs-theme`) arrived in 5.3, and the
  `--bs-body-*` variables they switch in 5.1, so 5.0.2 paints `.table`, `.modal`, `.dropdown-menu`, `.alert-*`, the form controls and
  the rest with literals, several of them inside SVG data URIs. AGENTS.md forbids editing it.
- On master 6564602 the project wrote its colours down in four places: 39 colour values in
  the declarations of `static/style.css` and 111 of the 474 `style=` attributes in the
  templates, score tints included (counting hex, `rgb()`/`hsl()` and CSS colour names), Python
  that baked colours for a
  white page (`ScoreColorProvider` blended every score tint toward white, `MatchReportRenderer`
  drew its PNGs on white, `functiondiff` handed the CFG viewer light fills), and black logos on
  transparency.

## Decision

1. **The preference is stored per user**: `user.theme` is `light`, `dark` or `system`. NULL,
   which every account from before #70 has, reads as `light`, so nobody's pages change until
   they pick something in Settings.
2. **The server decides, not a script.** `mcritweb/theme.py` turns the preference into one
   media query, and `base.html` puts it on the `<link>` of `static/theme-dark.css`: `all` for
   dark, `(prefers-color-scheme: dark)` for system, and no link at all for light. The same
   query goes on the `<source>` of every image that has a dark variant
   (`templates/table/themed_image.html`), and `<meta name="color-scheme">` tells the browser
   which scheme to paint the canvas and native controls in before any stylesheet arrives.
   Nothing flashes, nothing runs, and "system" follows the operating system live.
3. **Colours are named once.** `static/style.css` opens with a `:root` block that names every
   colour the project paints, with the values the pages always had; `theme-dark.css` gives the
   same names their dark values. Templates, project CSS and project JS use the names.
   `tests/testTheme.py` fails on a colour written anywhere else and on a name only one file
   defines.
4. **The vendored widgets are overridden from outside.** Part two of `theme-dark.css` maps
   Bootstrap, hint.css, Dropzone and the CFG viewer onto the names. Every rule there starts at
   `:root`, which outranks the vendored selector whatever order the stylesheets end up in.
5. **Data colours keep their meaning and lose their glare.** A score tint is computed in CSS,
   `color-mix()` of the hue into `--tint-ground` at `--tint-strength` (40% light, 28% dark),
   so the server never needs to know the theme. The CFG fills are names the Python hands out as
   `var(--cfg-...)`. A match diagram is drawn a second time as `<name>.dark.png`, on the dark
   ground with its hues mixed toward it. The cross-compare squares and the manual's screenshots
   are dimmed with a filter.

## Alternatives not taken

- **Upgrade Bootstrap to 5.3 for `data-bs-theme`.** It would replace the vendored library
  under every page for one feature, and 5.3 still leaves the project's own literals, the score
  tints, the diagrams and the CFG viewer to do.
- **`light-dark()` on every token.** Baseline only since 2024, so it needs the per-mode values
  as a fallback anyway, and a manual override would have to flip `color-scheme` per element.
- **A theme switcher in JavaScript.** It paints the light page first and swaps after load,
  which is the flash #70 asks to avoid.
- **Bake theme-specific colours on the server.** It cannot serve "system": the server does
  not know what the operating system prefers.

## Consequences

- A new colour needs a name in both files, and a test says so. Names that only the overrides
  of vendored widgets need are defined in `theme-dark.css` alone, and used nowhere else.
- `explore.fetchCombinedDotGraph` hands out the combined CFG as DOT whose colours are now
  `var(--cfg-...)` names, which Graphviz does not read; nothing in the UI offers it for
  download.
- The light pages are unchanged: the names carry master's values, and a score tint is the
  same 40% blend, give or take one step in 255 per channel where the browser rounds a
  `color-mix()` the Python used to truncate. One thing differs on purpose: the pickers
  painted a deselected row an inline white (`compare.html`, `compare_versus.html`,
  `cross_compare.html`, `unique_blocks.html`), which also gave its link the darker
  colour meant for tinted rows. A row's state is a class now, and a deselected row
  looks like one that was never selected.
- `color-mix()` needs Chrome 111, Firefox 113 or Safari 16.2 (Baseline 2023). An older browser
  shows the score cells untinted, in either theme.
- The diagram cache holds a second file per diagram once someone with the dark theme views it,
  and is no more bounded than before.
