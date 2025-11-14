"""Integration tests for the Terraform/Terragrunt module."""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import jubilant
import pytest

from tests.integration import helpers

logger = logging.getLogger(__name__)
pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).parent.parent.parent
TERRAFORM_MODULE_DIR = REPO_ROOT / "terraform" / "microceph"
APP_NAME = "microceph"
LOOP_OSD_SPEC = "1G,3"
LOOP_OSDS_PER_UNIT = int(LOOP_OSD_SPEC.split(",")[1])
DEFAULT_TIMEOUT = 7200


def _terraform_env(juju: jubilant.Juju) -> dict[str, str]:
    env = os.environ.copy()
    env["TF_IN_AUTOMATION"] = "1"
    env["JUJU_MODEL"] = _get_model_name(juju)
    return env


def _get_model_name(juju: jubilant.Juju) -> str:
    if not juju.model:
        raise ValueError("Juju model name unavailable")

    output = juju.cli("show-model", juju.model, "--format", "json", include_model=False)
    data = json.loads(output)
    if not isinstance(data, dict) or not data:
        raise ValueError("Unexpected juju show-model output")
    model_name = next(iter(data.keys()))
    if not isinstance(model_name, str):
        raise ValueError("Model name key missing from juju show-model output")
    return model_name


def _run_terragrunt(
    subcommand: str,
    env: dict[str, str],
    *extra_args: str,
    download_dir: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    command = [
        "terragrunt",
        "--non-interactive",
        subcommand,
        "-input=false",
        "-no-color",
    ]
    if subcommand in {"apply", "destroy"}:
        command.append("-auto-approve")
    command.extend(extra_args)
    if download_dir is not None:
        command.extend(["--download-dir", str(download_dir)])
    return subprocess.run(command, check=check, cwd=TERRAFORM_MODULE_DIR, env=env)


def _wait_for_microceph_units(juju: jubilant.Juju, *, expected_units: int) -> None:
    def _has_units(status: jubilant.Status) -> bool:
        app = status.apps.get(APP_NAME)
        if not app or not app.units:
            return False
        return len(app.units) >= expected_units

    juju.wait(_has_units, error=jubilant.any_error, timeout=DEFAULT_TIMEOUT)


def _wait_for_ceph_health_ok(
    juju: jubilant.Juju, timeout: int = DEFAULT_TIMEOUT
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_status: dict[str, Any] = {}
    while time.time() < deadline:
        last_status = helpers.fetch_ceph_status(juju, APP_NAME)
        health = last_status.get("health", {})
        if health.get("status") == "HEALTH_OK":
            return last_status
        time.sleep(15)
    raise AssertionError(f"Ceph health did not reach HEALTH_OK; last status: {last_status}")


class TerraformController:
    """Wrapper around terragrunt apply/destroy for the tests."""

    def __init__(self, juju: jubilant.Juju, env: dict[str, str], download_dir: Path):
        self._juju = juju
        self._env = env
        self._download_dir = download_dir
        self._known_units: set[str] = set()

    @property
    def juju(self) -> jubilant.Juju:
        return self._juju

    def apply(self, *, units: int) -> dict[str, Any]:
        """Apply the microceph terragrunt plan."""
        _run_terragrunt(
            "apply",
            self._env,
            "-var",
            f"units={units}",
            download_dir=self._download_dir,
        )
        _wait_for_microceph_units(self._juju, expected_units=units)
        helpers.wait_for_apps(self._juju, APP_NAME, timeout=DEFAULT_TIMEOUT)

        status = self._juju.status()
        app = status.apps.get(APP_NAME)
        if not app:
            raise AssertionError(f"Application {APP_NAME} not present after apply")

        current_units = set(app.units.keys())
        new_units = sorted(current_units - self._known_units)
        if new_units:
            # Adding loop osds on new units
            helpers.ensure_loop_osd(self._juju, APP_NAME, LOOP_OSD_SPEC, new_units)
        helpers.wait_for_apps(self._juju, APP_NAME, timeout=DEFAULT_TIMEOUT)
        self._known_units = current_units
        return _wait_for_ceph_health_ok(self._juju)

    def destroy(self) -> None:
        result = _run_terragrunt(
            "destroy", self._env, download_dir=self._download_dir, check=False
        )
        if result.returncode != 0:
            logger.warning("terragrunt destroy exited with %s", result.returncode)
        shutil.rmtree(self._download_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def terraform_controller(juju: jubilant.Juju) -> TerraformController:
    """Provide a reusable Terraform controller bound to the temp Juju model."""
    helpers.install_terraform_tooling()
    env = _terraform_env(juju)
    download_dir = Path(tempfile.mkdtemp(prefix="terragrunt-"))
    env = env.copy()
    env["TERRAGRUNT_DOWNLOAD"] = str(download_dir)
    _run_terragrunt("init", env, download_dir=download_dir)
    controller = TerraformController(juju, env, download_dir)
    yield controller


@pytest.mark.incremental
class TestTerraformScale:
    @pytest.mark.abort_on_fail
    @pytest.mark.smoke
    def test_initial_single_unit(self, terraform_controller: TerraformController) -> None:
        """Test: bring up a single unit with loop osds."""
        status = terraform_controller.apply(units=1)
        assert status.get("health", {}).get("status") == "HEALTH_OK"
        helpers.assert_osd_count(
            terraform_controller.juju,
            APP_NAME,
            expected_osds=LOOP_OSDS_PER_UNIT,
        )

    @pytest.mark.abort_on_fail
    @pytest.mark.smoke
    def test_scale_to_four_units(self, terraform_controller: TerraformController) -> None:
        """Test: scale microceph to 4 units with loop osds."""
        status = terraform_controller.apply(units=4)
        assert status.get("health", {}).get("status") == "HEALTH_OK"
        helpers.assert_osd_count(
            terraform_controller.juju,
            APP_NAME,
            expected_osds=4 * LOOP_OSDS_PER_UNIT,
        )
