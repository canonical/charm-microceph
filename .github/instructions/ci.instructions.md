---
applyTo: ".github/workflows/**,tests/scripts/**,charmcraft.yaml,tox.ini,pyproject.toml"
---

# CI / shell / charmcraft review instructions

Supplements the repo-wide instructions for workflow files, shell helpers, and build/tooling configuration.

## `charmcraft.yaml`

- **Platforms matrix** is amd64-only by deliberate choice. The charmcraft `uv` plugin (4.2.x at time of writing) installs **host-arch wheels** into cross-built artifacts regardless of `build-for`, producing silently corrupt charms for arm64/ppc64el/s390x. Flag any PR that re-adds those arches without either:
  - Citing a fixed charmcraft version that handles cross-builds, or
  - Adding per-arch native runners in the workflow.
- Each base requires its own `build-on`/`build-for` block. Multi-base output filenames are `microceph_ubuntu-<series>-<arch>.charm`. CI must consume those exact names.
- Don't reintroduce a single-base `base: ubuntu@X.Y` shortcut — the charm is committed to multi-base.

## GitHub Actions workflows (`.github/workflows/**`)

- **Charmcraft install:** workflows pin `--channel=latest/candidate`. The version check that was once added (`awk '{print $2}'` then a Python regex) is **deliberately removed** because the channel pin already guarantees `>= 4.1`. Flag any reintroduction.
- **Artifact glob expansion:** when listing packed charms, **always** use `shopt -s nullglob` + a bash array, then check `(( ${#charms[@]} == 0 ))`. The pattern `artifacts=$(printf '%s,' microceph_*.charm | ...)` silently passes when no charms were packed (the literal `microceph_*.charm` string is non-empty).
- **`charmcraft version` parsing:** never `awk '{print $2}'` — output format varies across snap revisions (`X.Y.Z` vs `charmcraft, version X.Y.Z`). If a parse is genuinely needed, regex `(\d+)\.(\d+)` from the full output.
- **Dual-channel publish (`main.yaml`):** each `upload-charm` call gets its own per-(base, arch) charmhub revision. The second upload sets `github-tag: false` to avoid colliding with tags created by the first. Don't claim "the same revisions" in comments — that's wrong.
- **Retry loops** around `snap install` and `charmcraft pack` are intentional (network and lxd flakiness). Don't simplify them away.

## Shell scripts (`tests/scripts/**`)

- `set -e` + pipelines: `cmd_a | cmd_b` under errexit fails fast on `cmd_a` returning non-zero, even when the intent was for `cmd_b` to handle the empty case. Use `var=$(cmd_a)` then `cmd_b <<< "$var"`, or `set +o pipefail` locally.
- `sed`/`grep` portability: use POSIX classes (`[[:space:]]`, `[[:digit:]]`) — never Perl-ish `\s`/`\d`. CI runs on GNU but local devs may be on macOS.
- `trap '... $tmp_dir' RETURN` inside a function: clears any prior RETURN trap. Document if doing so.
- `terragrunt --non-interactive <subcmd>` is broken on terragrunt 0.67+ (forwards the flag to terraform, rejected). Use `TERRAGRUNT_NON_INTERACTIVE=true terragrunt <subcmd>`.
- Pinned tool versions (`TERRAFORM_VERSION`, `TERRAGRUNT_VERSION`) should be overridable via env var. Hardcoded versions without an override hook are a 🔵 nit.

## `tox.ini`

- `[testenv:pyXY]` envs must match `pyproject.toml:requires-python`. PR #277 dropped py38/py39 when the range moved to `>=3.10,<3.13`; PR #278 should drop py310/py311 and add py313/py314 when the range moves to `>=3.12,<3.15`. Flag drift.
- `allowlist_externals = ... rename.sh` is deprecated and must not be reintroduced — `rename.sh` was deleted in the squid multi-base PR.

## `pyproject.toml`

- `requires-python` changes must be accompanied by a regenerated `uv.lock`. Run `uv lock --check` mentally: if dependencies that were Python-version-conditional drop out of the lockfile (eg `exceptiongroup` for >=3.11), that's expected, not a regression.
- `dependencies` ordering and pinning style: pin upper bounds for known-breaking deps (`ops<3.4`, `pydantic>=2.12,<2.13`, `requests<2.32`, `urllib3<2`); do not loosen these without a `Why:` in the PR body.

## What NOT to flag

- Self-hosted runner labels (`self-hosted-linux-amd64-noble-xlarge`) — managed externally.
- Sleep durations in retry loops — empirically tuned.
- LXD seeding/setup blocks — they're known-flaky and intentionally verbose.
