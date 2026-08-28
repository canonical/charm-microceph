---
applyTo: "**/*.py"
---

# Python review instructions (charm-microceph)

These rules supplement the repo-wide instructions for any Python file under `src/`, `tests/`, or top-level utilities.

## Charm code (`src/`)

- **Hooks must be idempotent.** Charm hooks fire repeatedly on the same relation events. Any state-changing call (snap install, microceph cluster join, remote import, OSD add) must either be a no-op on re-fire or explicitly catch the "already done" error. Flag new hook handlers that don't have this property.
- **`subprocess.CalledProcessError` handling.** When catching it to recover from "already exists" / "already configured" style errors:
  - Inspect both `exc.stdout` and `exc.stderr` (microceph CLI sometimes uses one, sometimes the other).
  - Use case-insensitive `re.search` rather than `"foo" in str(...)` — wording drifts across snap revisions.
  - Re-`raise` for any other returncode/message. Never swallow unconditionally.
  - Add a comment naming the exact upstream message and the snap version where it was observed.
- **Snap-track resolver (`src/microceph.py:MAJOR_VERSIONS`).** When adding a new release (eg `tentacle`), require:
  - Entry added to `MAJOR_VERSIONS` with the correct numeric key.
  - Test in `tests/unit/test_charm.py` covering `can_upgrade_snap("latest", "<new-track>")` with `mock_get_snap_info.return_value = {"latest": "<num>"}`.
- **`logger.info / .debug / .error` lines that include user-controlled strings** (relation app name, remote name, token excerpts) must not include the token itself. Flag any `logger.*` that interpolates `remote_token` or similar.
- **`ops` framework conventions:** charm classes inherit from a sunbeam-style base; observers register via `framework.observe(...)`. Don't add direct `self.on.X.observe(...)` calls in code paths that mix with sunbeam guards — flag and ask why.

## Test code (`tests/`)

- **`tests/conftest.py:_build_charm()`** must select the requested artifact by exact filename, not by mtime. Multi-base packing produces several `*.charm` files. Any "newest by mtime" pattern is a 🔴 bug.
- **`tests/helpers/__init__.py:ensure_charmcraft()`** asserts `charmcraft >= 4.1`. Flag any change that drops the assertion. CI workflows can rely on `latest/candidate` pinning, but local test runs cannot.
- **Harness mocks (`tests/unit/testbase.py`)**: `SnapCache` is patched to return a `MagicMock` with `.channel` set. The mock channel must match the charm's *current* default `snap-channel` from `config.yaml`. If `config.yaml` flips to `tentacle/edge`, the mock channel must move with it. Stale mocks let tests pass for the wrong reason.
- **`patch.return_value.__getitem__.return_value`** returns the same mock for *every* key. If a test reads multiple snap names, prefer `side_effect = lambda key: <dispatch>` so non-matching lookups raise.
- **Integration tests under `tests/integration/`** that call terragrunt: pass non-interactive via `TERRAGRUNT_NON_INTERACTIVE=true` env var, **not** `--non-interactive` flag. The flag form is forwarded to terraform on terragrunt 0.67+, which rejects it.

## Imports & style

- `subprocess` import must be at module top, not inside the function. If the function is the only consumer, still hoist it (this codebase prefers it).
- `re` likewise — top of file.
- Don't add `from __future__ import annotations` to modules that don't already have it; this codebase mixes both and lint accepts both.

## What NOT to flag

- Pydantic v1 patterns in `lib/charms/grafana_agent/v0/cos_agent.py` (vendored, deprecation warnings expected).
- `unittest.mock.patch` placement vs. `pytest-mock` style — both are used.
- Lack of type hints on tests — only flag missing hints in `src/`.
