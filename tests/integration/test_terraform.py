"""Integration tests for the Terraform/Terragrunt module."""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

import jubilant
import pytest

from tests import helpers

logger = logging.getLogger(__name__)
pytestmark = pytest.mark.slow


REPO_ROOT = helpers.find_repo_root(Path(__file__).resolve())
TERRAFORM_MODULE_DIR = REPO_ROOT / "terraform" / "microceph"
APP_NAME = "microceph"
LOOP_OSD_SPEC = "1G,3"
LOOP_OSDS_PER_UNIT = int(LOOP_OSD_SPEC.split(",")[1])
DEFAULT_TIMEOUT = 7200
ROOT_DISK_CONSTRAINT = "root-disk=32G"


@pytest.fixture(scope="module")
def juju_vm_constraints() -> tuple[str, ...]:
    """Override VM constraints for Terraform integration tests."""
    return ("virt-type=virtual-machine", "mem=4G", ROOT_DISK_CONSTRAINT)


@pytest.fixture(scope="module")
def juju(juju_vm: jubilant.Juju) -> jubilant.Juju:
    """Alias VM-backed Juju model fixture for this module."""
    return juju_vm


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


def _format_tf_map(values: Mapping[str, str]) -> str:
    if not values:
        raise ValueError("config map must not be empty")

    serialized = ",".join(f'"{key}"="{value}"' for key, value in sorted(values.items()))
    return "{" + serialized + "}"


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


def _microceph_services_snapshot(
    juju: jubilant.Juju, unit_name: str
) -> tuple[list[set[str]], str]:
    output = juju.ssh(unit_name, "sudo", "microceph", "status")
    services: list[set[str]] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("services:"):
            _, entries = stripped.split(":", 1)
            names = [svc.strip().lower() for svc in entries.split(",") if svc.strip()]
            services.append(set(names))
    return services, output


def _is_rgw_enabled(status: dict[str, Any]) -> bool:
    try:
        services = status["servicemap"]["services"]["rgw"]
    except (KeyError, TypeError):
        return False

    daemons = services.get("daemons")
    if isinstance(daemons, dict):
        return bool(daemons)
    if isinstance(daemons, list):
        return bool(daemons)
    return False


def _wait_for_microceph_status_rgw(
    juju: jubilant.Juju, unit_name: str, *, expected_nodes: int, timeout: int = DEFAULT_TIMEOUT
) -> str:
    """Wait until we have enough rgw service entries.

    We poll `microceph status` on some unit repeatedly until the total count of RGW
    services is at least *expected_nodes*.

    Fail if we don't reach this cond in time.
    """
    deadline = time.time() + timeout
    last_output = ""
    while time.time() < deadline:
        services, output = _microceph_services_snapshot(juju, unit_name)
        rgw_count = sum(1 for svc in services if "rgw" in svc)
        if rgw_count >= expected_nodes:
            return output
        last_output = output
        time.sleep(15)
    raise AssertionError(
        "RGW not listed for enough nodes in microceph status; last output:\n" + last_output
    )


def _wait_for_ceph_status_rgw(
    juju: jubilant.Juju, timeout: int = DEFAULT_TIMEOUT
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_status: dict[str, Any] = {}
    while time.time() < deadline:
        status = helpers.fetch_ceph_status(juju, APP_NAME)
        if _is_rgw_enabled(status):
            return status
        last_status = status
        time.sleep(15)
    raise AssertionError(f"Ceph status never reported RGW; last status: {last_status}")


def _assert_rgw_healthcheck(
    juju: jubilant.Juju, unit_name: str, timeout: int = DEFAULT_TIMEOUT
) -> None:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            juju.ssh(
                unit_name,
                "sudo",
                "curl",
                "-fsS",
                "--max-time",
                "15",
                "http://127.0.0.1:80/swift/healthcheck",
            )
            return
        except jubilant.CLIError as exc:  # type: ignore[attr-defined]
            last_error = exc.stderr or exc.stdout or str(exc)
        time.sleep(10)

    raise AssertionError(f"RGW health check failed on {unit_name}; last error: {last_error}")


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

    def apply(self, *, units: int, config: Mapping[str, str] | None = None) -> dict[str, Any]:
        """Apply the microceph Terragrunt plan with optional charm config overrides."""
        extra_args = ["-var", f"units={units}"]
        if config:
            extra_args.extend(["-var", f"config={_format_tf_map(config)}"])

        _run_terragrunt(
            "apply",
            self._env,
            *extra_args,
            download_dir=self._download_dir,
        )
        helpers.wait_for_microceph_units(
            self._juju, APP_NAME, expected_units=units, timeout=DEFAULT_TIMEOUT
        )
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

        # Pools like .mgr may miss application metadata in some environments,
        # which leads to persistent HEALTH_WARN and stalls the test.
        helpers.enable_missing_pool_apps(self._juju, APP_NAME)

        self._known_units = current_units
        return helpers.wait_for_ceph_health_ok(self._juju, APP_NAME, timeout=DEFAULT_TIMEOUT)

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


@pytest.mark.incremental
class TestTerraformRadosGateway:
    @pytest.mark.abort_on_fail
    def test_enable_rgw_via_config(self, terraform_controller: TerraformController) -> None:
        status = terraform_controller.apply(units=4, config={"enable-rgw": "*"})
        assert status.get("health", {}).get("status") == "HEALTH_OK"

        current_status = terraform_controller.juju.status()
        app = current_status.apps.get(APP_NAME)
        if not app or not app.units:
            raise AssertionError(f"Application {APP_NAME} missing units after RGW apply")

        unit_name = helpers.first_unit_name(current_status, APP_NAME)
        _wait_for_microceph_status_rgw(
            terraform_controller.juju,
            unit_name,
            expected_nodes=len(app.units),
        )
        rgw_status = _wait_for_ceph_status_rgw(terraform_controller.juju)
        assert _is_rgw_enabled(rgw_status)

    @pytest.mark.abort_on_fail
    def test_rgw_healthcheck(self, terraform_controller: TerraformController) -> None:
        status = terraform_controller.juju.status()
        unit_name = helpers.first_unit_name(status, APP_NAME)
        _assert_rgw_healthcheck(terraform_controller.juju, unit_name)

    @pytest.mark.abort_on_fail
    def test_rgw_object_io(self, terraform_controller: TerraformController) -> None:
        status = terraform_controller.juju.status()
        unit_name = helpers.first_unit_name(status, APP_NAME)
        helpers.exercise_rgw(terraform_controller.juju, unit_name)
