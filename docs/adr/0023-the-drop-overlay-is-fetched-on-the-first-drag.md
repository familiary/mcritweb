# The drop overlay is fetched on the first drag

---
status: accepted — measured 2026-10-26; carries on from the dropzone half of 0015, keeps 0021's queue
---

Every signed-in page but the upload forms carried the drop overlay: base.html's
`#submitFileModal`, the upload form inside it, the options flask-dropzone writes for it as a
script, the zone's style and the family type-ahead's tag. #126 had already left Dropzone's
library and stylesheet out of those pages until a file is dragged over them. The overlay
itself stayed, for the same moment, and it was 1.6 to 1.9 KB of each page gzipped: on the
offline corpus `/` was 5,694 bytes and is 3,926 without it, a sample's matches 12,742 and
10,814, `/explore/samples` 9,931 and 8,149. With the other open changes on top, a result page
was 14.1 KB against the 14.6 KB a first round trip carries (ADR-0022), and is 12 KB.

## What is done

base.html leaves an empty, hidden `#drop-overlay-slot` that names `data.drop_overlay` and
Dropzone's two files, and queues `static/upload_form.js` as before. On the first dragover that
carries a file, `mcritLoadDropzone()` asks for the overlay and for Dropzone at the same time,
puts the overlay after the slot once both are in, wires its form as the upload pages' form is
wired, and attaches Dropzone; a second drag finds it there. `data.drop_overlay` renders
`templates/drop_overlay.html` with the same `submit_or_query_dropzone(lazy=True)` macro the
overlay had, signed in only, `Cache-Control: no-store`, with the session's token masked
afresh in each answer.

The overlay holds no script. A script in markup that a script inserts does not run, and under
a nonce policy with `'strict-dynamic'` it would not be let run if it did. So what its scripts
did comes from `upload_form.js`, from data- attributes the macro writes on the form:

- the Dropzone options, as JSON, built by `views/data.py`'s `drop_overlay_options()` from the
  settings flask-dropzone's `dropzone.config()` reads, with the arguments the macro passes
  it. `tests/testDropOverlay.py` compares them with the script the query page gets.
- the family type-ahead: a page with an edit modal queues `static/family_names.js` already,
  and it attaches to the overlay's field on its first focus; on any other page
  `upload_form.js` loads it when the overlay is in. Those pages no longer load it at all
  until then.
- the minhash slider's labels, for the slider's handler, which the upload pages define
  inline so that the label follows the slider before `upload_form.js` has run.

A file let go while the overlay is on its way is the page's: it is kept and handed to the
zone once Dropzone is in. Before, nothing took a drop until the overlay was shown, and the
browser opened the file in place of the page or, with nothing to open it, dropped it.

## Why a fetched fragment, and not the form built in script

The form is the macro's, and the query and submit pages render it too. Built in script it
would be written twice, and every change to the form - the open changes add labels,
fieldsets and a status line to it - would have to be made in both. Fetched, it is one
template. The fetch runs alongside Dropzone's 77 KB, which it does not hold up: at 150 ms
round trips, 1.6 Mbps and a CPU four times slower, the dialog opened 1,075 to 1,190 ms after
the first dragover with the overlay in the page and 951 to 1,111 ms with it fetched, three
runs a side. A `<template>` in the page would have saved nothing, since its markup is sent
with the page all the same.

No new file is queued on every page: Lighthouse counts 20 to 40 ms of blocking time on a
phone for each queued file, however small. The loader stays in `upload_form.js`, which the
overlay pages queue already.

## Consequences

- Without script nothing changes: the overlay was opened by script before.
- A session that has ended answers the overlay's request with the login page; nothing is put
  in the page, and the next drag asks again, as after a failed fetch of Dropzone.
- The overlay's request is one more request on the first drag of each page; it is not kept,
  because it carries the session's token.
- The query, submit and import pages are as they were: their content is the drop zone, and
  it is rendered with them.
