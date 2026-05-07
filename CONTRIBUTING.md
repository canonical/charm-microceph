# Contributing

To make contributions to this charm, you'll need a working [development setup](https://juju.is/docs/sdk/dev-setup).

You can use the environments created by `tox` for development:

```shell
tox --notest -e unit
source .tox/unit/bin/activate
```

## Sign off your commits (DCO)

Every commit must carry a `Signed-off-by:` trailer matching the commit
author, certifying the [Developer Certificate of Origin](https://developercertificate.org/).
Use `git commit -s` (or `--signoff`) to add it automatically.

This repository ships a `commit-msg` hook in `.githooks/` that rejects
unsigned commits locally before they are pushed. After cloning, point git
at it once:

```shell
git config core.hooksPath .githooks
```

The hook is a plain `bash` script with no extra dependencies.

## Testing

This project uses `tox` for managing test environments. There are some pre-configured environments
that can be used for linting and formatting code when you're preparing contributions to the charm:

```shell
tox -e fmt           # update your code according to linting rules
tox -e lint          # code style
tox -e unit          # unit tests
tox -e integration   # integration tests
tox                  # runs 'lint' and 'unit' environments
```

Integration tests deploy a generic Ceph client test charm. The default
implementation is `johnny` from Charmhub (`edge` channel). For local
development you can override that with either a built charm artifact or a local
source checkout:

```shell
export CLIENT_CHARM=/path/to/client.charm
# or
export CLIENT_SOURCE=/path/to/client-source
# optional Charmhub overrides
export CLIENT_NAME=johnny
export CLIENT_CHANNEL=edge
```

If you keep a sibling `johnny` checkout next to this repository, the integration
fixtures will automatically build and use it.

## Build the charm

Build the charm in this git repository using:

```shell
charmcraft pack
```

<!-- You may want to include any contribution/style guidelines in this document>
