# Releasing MCRITweb

This repository follows the release process shared across the MCRIT ecosystem
([smda](https://github.com/danielplohmann/smda), [purepdb](https://github.com/danielplohmann/purepdb),
[mcrit](https://github.com/danielplohmann/mcrit), [mcritweb](https://github.com/fkie-cad/mcritweb),
[mcrit-plugin](https://github.com/danielplohmann/mcrit-plugin),
[docker-mcrit](https://github.com/danielplohmann/docker-mcrit)). The shape is the same everywhere;
this file states the values that are specific to this repository.

## Versioning

MCRITweb follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html) over what a
deployment sees - the routes, the configuration keys and the SQLite schema; see the note at the top
of `CHANGELOG.md`. MCRITweb is deployed from a checkout and is not published to PyPI, so the
release is the tag and the GitHub release page.

The version is declared in one place, `pyproject.toml` (`[project].version`), and read at runtime
by `get_mcritweb_version()`. The release workflow refuses a tag that does not match it, so a tag
cut before the bump fails before anything is published.

## What differs from the shared process

| | MCRITweb |
|---|---|
| Version scheme | Semantic Versioning over the routes, the `instance/config.py` keys and the SQLite schema |
| Where the version lives | `pyproject.toml` only |
| Changelog heading | `## [X.Y.Z] - YYYY-MM-DD`, without the `v` (`## [vX.Y.Z]` is accepted too) |
| Release workflow | `.github/workflows/release.yml`, on a pushed `v*` tag |
| CI gate | the `test.yml` workflow (*CI*) has to have passed on the tagged commit |
| Package index | none: no PyPI, no trusted publishing, no `pypi` environment, no TestPyPI rehearsal |
| Build check | the wheel is installed into a clean environment to prove the templates, static assets and SQL schema ship with it |
| Milestones | not used; the release does not check or close one |
| Python | 3.12 to 3.14, all in CI; the release builds on 3.12 |
| Backend floor | `mcrit>=1.12.0`, in `pyproject.toml` and `requirements.txt` |

## Supported Python versions

MCRITweb supports Python 3.12 through 3.14, and CI runs every one of them. The floor is inherited
from mcrit, which has declared `>=3.12` since v1.10.0; nothing in MCRITweb's own source needs it.
`tests/testPythonFloor.py` holds `requires-python`, the ruff `target-version`, the README,
the CI matrix and the installed mcrit's own metadata to the same floor, so raising it means moving all of them in one
change, with a `Removed` entry in the changelog.

## Changelog

`CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). It is the one
authoritative record of what a release contains: the GitHub release notes are generated from it, and
nothing is written twice.

- Every pull request that changes something a user can observe adds its own bullet under
  `## [Unreleased]`, in the subsection it belongs to (`Added`, `Changed`, `Deprecated`, `Removed`,
  `Fixed`, `Security`), while the change is fresh. The `Changelog` check fails a PR that touches
  shipped files without touching `CHANGELOG.md`; apply the `no-changelog` label when a change
  genuinely needs no entry (a typo, a CI-only change), and say why in the PR.
- An entry says what changed and what it costs the reader: what to do when upgrading, what may
  behave differently, which issue or PR it closes.
- Dependency bumps need no entry. GitHub lists them under their own heading in the release notes,
  from the `dependencies` / `github_actions` labels (`.github/release.yml`).

## Cutting a release

1. Check that `master` is green and that everything meant for the release has merged.
2. In one commit on a branch, then merged through a PR:
   - set the new version in `pyproject.toml` (`[project].version`, read at runtime by `get_mcritweb_version()`);
   - in `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, drop the empty
     subsections, open a fresh empty `## [Unreleased]` above it, and update the compare links at
     the foot of the file.
3. Wait for CI to pass on the merge commit. Then tag that commit and push the tag:

   ```bash
   git tag -a vX.Y.Z -m "MCRITweb X.Y.Z"
   git push origin vX.Y.Z
   ```

Pushing the tag is the release. `.github/workflows/release.yml` then:

1. **Verify** — refuses to continue unless the tag matches `pyproject.toml`, `CHANGELOG.md` has a
   `## [X.Y.Z] - <date>` section (which becomes the release notes), the tagged commit is on
   `master`, and CI passed on that commit.
2. **Build** — builds the sdist and wheel, checks their metadata with `twine check --strict`, and
   installs the wheel into a clean virtual environment to prove the templates, static assets and
   schema ship with the package. Nothing is published to a package index.
3. **Release** — creates the GitHub release for the tag with the changelog section as its body,
   GitHub's generated contributor and PR list appended under it, and the sdist and wheel attached.

Each gate fails with a message naming what to fix. Nothing has to be remembered at the console.

## Pre-releases

A release candidate is tagged `vX.Y.Zrc1` (also `a1`, `b1`), with the same version string in
`pyproject.toml` (`[project].version`, read at runtime by `get_mcritweb_version()`) and a `## [X.Y.Zrc1] - YYYY-MM-DD` changelog section. The workflow marks the
GitHub release as a pre-release and does not make it "latest".

## Rehearsing

There is no index to rehearse against. The gates and the build run on every tag before the release
is created, and a tag that fails a gate leaves nothing behind; delete it, fix the cause, and tag
again.

## When a release fails

- **A gate failed before anything was published** (tag/version mismatch, missing changelog section,
  tag not on `master`, CI not green): fix the cause on `master`, delete the
  tag locally and on the remote (`git push --delete origin vX.Y.Z`), and tag again once the fix has
  merged. Nothing needs cleaning up.

- **The GitHub release step failed after publishing**: re-run only the failed job from the Actions
  UI; the built artifacts are kept as workflow artifacts and the step is idempotent.

## Maintainer configuration

Done once, by a repository owner; the workflow cannot create these for itself.

- **Label** `no-changelog`, used by the changelog check.
- Optionally, **immutable releases** (Settings → General → Releases), so a published release's
  assets and tag can no longer be changed.

## Release order across the ecosystem

MCRITweb depends on `mcrit` (`>=1.12.0`) and consumes its data classes directly, and
`docker-mcrit` pins an exact MCRIT and MCRITweb version in its `.env`. When a change here needs
new backend behaviour, release MCRIT first, raise the floor in `pyproject.toml` and
`requirements.txt`, release MCRITweb, then bump `docker-mcrit`.
