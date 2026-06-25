# AGENTS.md

## Commit conventions

- Commits must be signed off (`Signed-off-by:` trailer) **by the human**. Agents must never add a `Signed-off-by:` trailer on the human's behalf — the DCO sign-off is an attestation only the human can make.
- Agents must include an `Assisted-by:` trailer identifying the agent and model.
- Order trailers as: `Assisted-by:` first, then the human's `Signed-off-by:` last (added by the human).

Format:

```
Assisted-by: AGENT_NAME:MODEL_VERSION
```

- `AGENT_NAME` — the AI tool or framework (e.g. `claude-code`, `opencode`, `codex`, `pi`, …).
- `MODEL_VERSION` — the specific model version used (e.g. `claude-sonnet-4-6`, `gpt-5.5`).

Example:

```
Assisted-by: opencode:gpt-5.5
```

Other commit rules:

- Commit messages must be ASCII only.
- Keep PRs small and focused; don't mix trivial and controversial changes.
- Squash into logical commits (API / docs / CLI / daemon / tests / CI) for non-trivial PRs.
- Maintain a linear git history.

## Pull request conventions

- Pull request descriptions must always conform to the project's PR template at [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md). Fill in every section of the template rather than replacing it with free-form text.
- The repo ships a DCO `commit-msg` hook at `.githooks/commit-msg`, only active when the human has run `git config core.hooksPath .githooks`. If it's enabled in your clone, pass `git commit --no-verify` — agents can't add the sign-off, so the hook would otherwise block every agent-authored commit.

## Coding conventions (non-obvious)

- Line length **99** (black + isort with `profile = "black"`).

## Build

```shell
tox -e build          # produces microceph_*.charm at repo root
```

## Test & lint

```shell
tox -e fmt    # apply isort + black
tox -e lint   # codespell + pflake8 + isort --check + black --check
tox -e py3    # pytest over tests/unit + tests/selftests
tox           # pep8 (= lint) + py3
```
