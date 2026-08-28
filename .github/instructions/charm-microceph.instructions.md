---
applyTo: "**"
---

# Charm-MicroCeph review instructions

Repo-wide guidance for Copilot review on `canonical/charm-microceph`. These rules are derived from incidents and lessons that have shipped in this repo. Apply them on every review pass before flagging style nits.

## Project shape (read before reviewing)

- Charmed operator wrapping the **MicroCeph** snap. Distributed as a charm via Charmhub.
- Built with `charmcraft pack` against a multi-base `charmcraft.yaml` (currently amd64-only). Artifacts are named per (base, arch): `microceph_ubuntu-<series>-<arch>.charm`. There is **no** `microceph.charm` and there is **no** `rename.sh` — both were removed in the squid multi-base PR. Flag any reintroduction.
- Snap-track convention: `<release>/<risk>` (eg `squid/stable`, `tentacle/edge`). Default channel lives in `config.yaml` and the Terraform module under `terraform/microceph/`.
- Python is managed with `uv` and `pyproject.toml`. `requires-python` declares the supported range; tox envs in `tox.ini` must match it. Flag any `py3X` env outside the declared range.
- Charmcraft version: builds require `charmcraft >= 4.1`. CI installs from `latest/candidate`. Local pytest uses `tests/helpers/__init__.py:ensure_charmcraft()` which asserts the version.

## Review priorities

Always check, in order:

1. **Correctness on real input shapes**, not idealized ones (see "Common traps" below).
2. **Idempotency of hook handlers**. Charm hooks fire repeatedly; any state-changing call must converge across re-fires. The microceph remote-import path is the canonical example — re-firing produces "already exists"; non-idempotent code leaves the unit in error state.
3. **Cross-file consistency**. A `pyproject.toml` change must be reflected in `tox.ini` envs and `uv.lock`. A `charmcraft.yaml` platforms change must be reflected in CI artifact handling, bundle paths, and any test fixture filenames.
4. **CI vs local-dev divergence**. Workflows pin `latest/candidate` charmcraft so a version check there is redundant; local helpers don't pin so they keep the assertion. Don't conflate the two.
5. **Backwards-compat hacks** the codebase has explicitly chosen to drop (eg `rename.sh`, `microceph.charm`, py38/py39 envs). Flag any reintroduction.

## Common traps (auto-flag if seen)

These have all hit this repo. Treat any reappearance as a 🟡 risk minimum.

- `built_charms = sorted(glob("*.charm"), key=mtime, reverse=True)[0]` then rename — multi-base produces multiple charms; mtime picks the wrong base. Require exact filename match.
- `artifacts=$(printf '%s,' microceph_*.charm | sed ...)` — unmatched glob expands to the literal pattern, masking the no-artifacts case. Require `shopt -s nullglob` + bash array.
- `grep -v <pattern> "$file" | grep -qxF <other>` under `set -e` — first grep returning 1 (no matches left) trips errexit before the second grep runs. Require `sed`-strip into a variable, then single grep.
- `sed '/^\s*#/d'` — `\s` is GNU-only. Require `[[:space:]]` for POSIX/BSD/macOS portability.
- `version=$(charmcraft version | awk '{print $2}')` — `charmcraft version` may print `X.Y.Z` or `charmcraft, version X.Y.Z` depending on snap revision. Require regex extraction.
- Catching `subprocess.CalledProcessError` and matching stderr substrings without case-insensitivity or regex anchoring — CLI output wording shifts across versions. Require explicit pattern + version pin or a defensive comment.

## Don't flag

- Files in `lib/charms/` (vendored charm libs; managed by `charmcraft fetch-libs`).
- `uv.lock` line-by-line. Trust `uv lock --check` in CI; only flag if the diff suggests deps fell out unexpectedly (the supported-python range change in PR #278 is the canonical example).
- Style nits in test fixtures (data classes, mocked CA certs, hex blobs).

## Severity guidance

- 🔴 bug: incident-class. Mtime-newest rename, swallowed `CalledProcessError` that masks unrelated failure, CI step that silently passes when no artifacts exist.
- 🟡 risk: works today, fragile. Substring matches without case folding, hardcoded mocks that drift from production defaults, version checks that the surrounding context already guarantees.
- 🔵 nit: micro-optimization, naming, formatting. Comment count should not exceed 1-2 per review.
- ❓ q: genuine ambiguity. Use sparingly; prefer to read `git log` / `git blame` first.

## Output format

One line per finding: `path:Lline: <emoji> <severity>: <problem>. <fix>.` No throat-clearing, no restating of what the diff does, no closing summary.
