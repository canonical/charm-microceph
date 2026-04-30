"""Pytest + jubilant fixtures for integration testing."""

import json
import os
from pathlib import Path
from typing import Iterator, NamedTuple

import jubilant
import pytest
import yaml

from tests import helpers
from tests.conftest import _build_charm

REPO_ROOT = helpers.find_repo_root(Path(__file__).resolve())
DEFAULT_CLIENT_CHANNEL = "edge"
DEFAULT_CLIENT_NAME = "johnny"
DEFAULT_JUJU_VM_SPACE = "jujuspace"


class CharmDeployment(NamedTuple):
    """Charm reference and optional channel to deploy."""

    charm: str
    channel: str | None = None


def _artifact_name_for_source(source_dir: Path) -> str:
    """Return the built charm artifact name for a client charm source checkout."""
    metadata = yaml.safe_load((source_dir / "metadata.yaml").read_text())
    return f"{metadata['name']}.charm"


def _available_juju_space_names(juju: jubilant.Juju) -> set[str]:
    """Return known Juju space names for the current controller/model."""
    output = juju.cli("spaces", "--format", "json")
    payload = json.loads(output or "[]")

    if isinstance(payload, dict):
        raw_spaces = payload.get("spaces", [])
    elif isinstance(payload, list):
        raw_spaces = payload
    else:
        raw_spaces = []

    names: set[str] = set()
    for entry in raw_spaces:
        if isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str) and name:
                names.add(name)
        elif isinstance(entry, str) and entry:
            names.add(entry)
    return names


def _preferred_juju_space(juju: jubilant.Juju) -> str | None:
    """Prefer the cephtools management space when it exists.

    In the cephtools-backed MAAS/LXD environment, unconstrained workload models can
    end up provisioning instances on the auxiliary external network, where MAAS VM
    deployment may stall during ephemeral boot. If the dedicated Juju management
    space exists, pin VM-backed test models to it and make it the model default
    so application endpoint bindings do not fall back to the empty "alpha" space.
    """
    preferred_space = os.getenv("JUJU_VM_SPACE", DEFAULT_JUJU_VM_SPACE).strip()
    if not preferred_space:
        return None

    try:
        available_spaces = _available_juju_space_names(juju)
    except (jubilant.CLIError, json.JSONDecodeError):
        return None

    if preferred_space not in available_spaces:
        return None
    return preferred_space


@pytest.fixture(scope="session")
def cephclient_deployment() -> CharmDeployment:
    """Return how integration tests should deploy the Ceph client charm.

    By default this resolves to the `johnny` charm, but callers can override
    the artifact, source checkout, Charmhub name, and channel via `CLIENT_*`
    environment variables.
    """
    artifact_override = os.environ.get("CLIENT_CHARM")
    if artifact_override:
        artifact = Path(artifact_override).expanduser().resolve()
        if not artifact.exists():
            raise FileNotFoundError(f"Ceph client charm override not found: {artifact}")
        return CharmDeployment(str(artifact))

    source_override = os.environ.get("CLIENT_SOURCE")
    if source_override:
        source_dir = Path(source_override).expanduser().resolve()
        if not source_dir.exists():
            raise FileNotFoundError(f"Ceph client source override not found: {source_dir}")
        artifact = _build_charm(source_dir, artifact_name=_artifact_name_for_source(source_dir))
        return CharmDeployment(str(artifact))

    sibling_source = REPO_ROOT.parent / "johnny"
    if (sibling_source / "charmcraft.yaml").exists():
        artifact = _build_charm(
            sibling_source,
            artifact_name=_artifact_name_for_source(sibling_source),
        )
        return CharmDeployment(str(artifact))

    return CharmDeployment(
        os.environ.get("CLIENT_NAME", DEFAULT_CLIENT_NAME),
        os.environ.get("CLIENT_CHANNEL", DEFAULT_CLIENT_CHANNEL),
    )


@pytest.fixture(scope="module")
def juju_vm_constraints() -> tuple[str, ...]:
    """Default VM constraints for integration tests that need VM machines."""
    return ("virt-type=virtual-machine", "mem=4G", "root-disk=16G")


@pytest.fixture(scope="module")
def juju_vm(
    request: pytest.FixtureRequest,
    juju_vm_constraints: tuple[str, ...],
) -> Iterator[jubilant.Juju]:
    """Provide a temporary Juju model configured for VM-based tests."""
    keep_models = bool(request.config.getoption("--keep-models"))
    with jubilant.temp_model(keep=keep_models) as juju:
        juju.wait_timeout = 60 * 60
        constraints = list(juju_vm_constraints)
        preferred_space = _preferred_juju_space(juju)
        if preferred_space:
            juju.cli("model-config", f"default-space={preferred_space}")
            space_constraint = f"spaces={preferred_space}"
            if space_constraint not in constraints:
                constraints.append(space_constraint)
        juju.cli("set-model-constraints", *constraints)
        yield juju
        if request.session.testsfailed:
            log = juju.debug_log(limit=1000)
            if log:
                print(log, end="")
