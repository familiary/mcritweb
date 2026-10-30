# Hand-roll CSRF, shaped so flask-wtf can replace it

---
status: accepted — intended to be replaced, see #27
---

Issue #83 needed CSRF protection. The obvious answer is `flask-wtf`'s `CSRFProtect`,
and it is the right long-term answer. We wrote `mcritweb/csrf.py` instead, roughly
sixty lines: a random token per session, a `before_request` check on every method
that is not `GET`/`HEAD`/`OPTIONS`/`TRACE`, and an `exempt()` for `/api`.

Two reasons, in order of weight:

1. **Flask is hard-pinned at 2.2.5** (ADR-0001). Adding a dependency that has to
   resolve against a 2023 Flask, when the plan is to lift that pin in #27, means
   pinning `flask-wtf` too and then moving both. The extension is easier to adopt
   *after* the upgrade than before it.
2. **`wtforms` comes along for nothing.** This application builds no WTForms forms
   and would gain none; `CSRFProtect` is the only part of the package it wants.

None of that is an argument that hand-rolled is better. It is an argument about
sequencing.

## What makes the swap cheap

The module deliberately mirrors the public surface of `CSRFProtect` rather than
inventing its own:

| Ours | flask-wtf |
| --- | --- |
| `csrf_token()` template global | same name |
| `csrf_token` form field | `WTF_CSRF_FIELD_NAME` default |
| `X-CSRFToken` / `X-CSRF-Token` headers | same |
| `WTF_CSRF_ENABLED` config key | same |
| `CsrfProtect(app)`, `.exempt(view_or_blueprint)` | `CSRFProtect(app)`, `.exempt(...)` |
| `app.extensions["csrf"]` | same |

So adopting the extension is: delete `mcritweb/csrf.py`, change two imports in the
app factory, add the dependency. **No template changes, no JavaScript changes, no
test changes.** The last row is what makes Flask-Dropzone work — it refuses to send
a token unless it finds a protector under `app.extensions["csrf"]`, and it will find
either one.

One later addition has no counterpart: `header_only()` (#284), which keeps the check
out of `analyze.query`'s form, so an upload over the caller's cap is refused before it
is parsed. flask-wtf reads the form before the headers, so under it the same takes
exempting that view and calling `csrf.protect()` from it once the cap is checked.

## Consequences

What the extension provides and this does not, none of it the primary defence:

- tokens are raw session values, not signed and time-limited — so a token is valid
  for the life of the session;
- no referrer check on HTTPS requests.

Per-response token variation was on this list too, until pages were to be gzipped
(`mcritweb/compression.py`): a secret identical in every response, compressed next to
input the page reflects, is what a BREACH attack reads out of the response's length.
`csrf_token()` now returns the session token masked behind a fresh random pad on every
call, as hex of the pad followed by the token XOR the pad, the scheme Rails uses, and
the check unmasks what it is sent. The bare token is still accepted, so a page
rendered before the change keeps working. Still hand-rolled, still the same names, so
the swap to flask-wtf described above is unchanged.

The token's strength rests on the session cookie's signature, and so on
`SECRET_KEY`. Fixing the `'dev'` default (`mcritweb/secret_key.py`) was part of the
same change for that reason: without it, an attacker who can forge a session can put
whatever token they like inside it, and none of the above matters.

`SESSION_COOKIE_SAMESITE = "Lax"` is set as defence in depth. It is not a substitute
— it still permits a top-level `GET`, which is exactly what the routes tracked in
issue #97 are vulnerable to.
