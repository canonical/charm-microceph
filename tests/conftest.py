"""Pytest + jubilant fixtures for testing."""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Iterator

import jubilant
import pytest

from tests import helpers
from tests.helpers.qualification import Qualification, test_identity

REPO_ROOT = helpers.find_repo_root(Path(__file__).resolve())
DEFAULT_JUJU_BASE = "ubuntu@24.04"
JUJU_BASE_PATTERN = re.compile(r"^ubuntu@[0-9]{2}\.[0-9]{2}$")


def _juju_base_from_env() -> str:
    """Return and validate the Juju base requested for integration tests."""
    juju_base = os.environ.get("JUJU_BASE", DEFAULT_JUJU_BASE)
    if not JUJU_BASE_PATTERN.fullmatch(juju_base):
        raise pytest.UsageError(
            f"Invalid JUJU_BASE {juju_base!r}; expected a value such as ubuntu@24.04"
        )
    return juju_base


def _artifact_name_for_juju_base(juju_base: str) -> str:
    """Return the amd64 charm artifact name for a Juju base."""
    name, channel = juju_base.split("@", maxsplit=1)
    return f"microceph_{name}-{channel}-amd64.charm"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add options."""
    parser.addoption("--qualification-manifest", default=None)
    parser.addoption("--qualification-evidence", default=None)
    parser.addoption(
        "--keep-models",
        action="store_true",
        default=False,
        help="Do not destroy the temporary Juju models created for integration tests.",
    )
    parser.addoption(
        "--sunbeam-model",
        action="store",
        default="sunbeam-controller:admin/openstack-machines",
        help="Existing Juju model to attach the Sunbeam end-to-end suite to.",
    )
    parser.addoption(
        "--sunbeam-app",
        action="store",
        default="microceph",
        help="Application name to target inside the attached Sunbeam model.",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Enable optional release evidence only for an explicit, pinned manifest."""
    config._microceph_qualification = None
    manifest_path = config.getoption("--qualification-manifest")
    if not manifest_path:
        return
    evidence_path = config.getoption("--qualification-evidence")
    if not evidence_path:
        raise pytest.UsageError("Qualification evidence path is required")
    manifest = json.loads(Path(manifest_path).read_text())
    actual_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
    ).strip()
    if actual_sha != manifest.get("source_sha"):
        raise pytest.UsageError("Test source SHA does not match qualification manifest")
    config._microceph_qualification = Qualification(manifest, Path(evidence_path))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item):
    """Audit actual snap identities while models still exist, before teardown."""
    qualification = getattr(item.config, "_microceph_qualification", None)
    juju = item.funcargs.get("sunbeam_juju") or item.funcargs.get("juju")
    app = item.funcargs.get("sunbeam_app_name", "microceph")
    if qualification:
        if juju is None:
            raise AssertionError("Qualification test has no Juju deployment")
        qualification.audit(juju, app, test_identity(item.nodeid), "before")
    outcome = yield
    if qualification and outcome.excinfo is None:
        try:
            qualification.audit(juju, app, test_identity(item.nodeid), "after")
        except Exception as error:
            outcome.force_exception(error)


def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> None:
    """Abort the test session if an abort_on_fail test fails.

    pytest-operator backwd. compat.
    """
    if call.when == "call" and call.excinfo and item.get_closest_marker("abort_on_fail"):
        item.session.shouldstop = "abort_on_fail marker triggered"


@pytest.fixture(scope="session")
def juju_base() -> str:
    """Return the Juju base used by integration models and deployments."""
    return _juju_base_from_env()


@pytest.fixture(scope="module")
def juju(request: pytest.FixtureRequest, juju_base: str) -> Iterator[jubilant.Juju]:
    """Provide a temporary Juju model managed by Jubilant."""
    keep_models = bool(request.config.getoption("--keep-models"))
    with jubilant.temp_model(
        keep=keep_models,
        config={"default-base": juju_base},
    ) as juju:
        juju.wait_timeout = 20 * 60
        yield juju
        if request.session.testsfailed:
            log = juju.debug_log(limit=1000)
            if log:
                print(log, end="")


def _build_charm(
    charm_dir: Path,
    *,
    artifact_name: str,
    rebuild: bool = True,
) -> Path:
    """Build a charm at *charm_dir* and return the resulting artifact."""
    artifact = charm_dir / artifact_name
    if not rebuild and artifact.exists():
        return artifact.resolve()

    helpers.ensure_charmcraft()
    subprocess.run(["charmcraft", "-v", "pack"], check=True, cwd=charm_dir)

    # Multi-base charmcraft.yaml emits one artifact per (base, arch); the
    # caller specifies which one it wants by exact filename. Prefer that
    # exact match rather than picking the newest *.charm by mtime, which
    # could grab the wrong base when both jammy and noble are produced.
    if artifact.exists():
        return artifact.resolve()

    built_charms = list(charm_dir.glob("*.charm"))
    if not built_charms:
        raise FileNotFoundError(f"No charm artifacts produced in {charm_dir}")
    raise FileNotFoundError(
        f"Expected charm artifact {artifact_name} not produced in {charm_dir}; "
        f"found: {sorted(c.name for c in built_charms)}"
    )


@pytest.fixture(scope="session")
def microceph_charm(juju_base: str) -> Path:
    """Return the built MicroCeph charm artifact for the selected Juju base."""
    return _build_charm(
        REPO_ROOT,
        artifact_name=_artifact_name_for_juju_base(juju_base),
        rebuild=False,
    )
