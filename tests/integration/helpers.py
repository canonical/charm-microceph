"""Shared helper functions for integration tests."""

import contextlib
import functools
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib import request

import jubilant
from tenacity import retry, stop_after_delay, wait_fixed

CEPHTOOLS_URL = "https://github.com/canonical/cephtools/releases/download/latest/cephtools"
CEPHTOOLS_PATH = Path("/usr/local/bin/cephtools")
DEFAULT_TIMEOUT = 1200


@functools.lru_cache(maxsize=1)
def install_terraform_tooling() -> None:
    """Install Terraform and Terragrunt via the cephtools helper."""
    cephtools_path = _ensure_cephtools_binary()
    subprocess.run(["sudo", str(cephtools_path), "terraform", "install-deps"], check=True)


def _ensure_cephtools_binary() -> Path:
    """Ensure the cephtools binary exists locally and is executable."""
    if CEPHTOOLS_PATH.is_file() and os.access(CEPHTOOLS_PATH, os.X_OK):
        return CEPHTOOLS_PATH

    _download_cephtools_binary(CEPHTOOLS_PATH)
    return CEPHTOOLS_PATH


def _download_cephtools_binary(destination: Path) -> None:
    """Download the cephtools binary to *destination* using curl-equivalent semantics."""
    fd, tmp_name = tempfile.mkstemp()
    os.close(fd)
    tmp_path = Path(tmp_name)

    try:
        with request.urlopen(CEPHTOOLS_URL) as response, tmp_path.open("wb") as target:
            shutil.copyfileobj(response, target)
        subprocess.run(
            ["sudo", "install", "-m", "0755", str(tmp_path), str(destination)], check=True
        )
    finally:
        tmp_path.unlink(missing_ok=True)


@contextlib.contextmanager
def fast_forward(juju: jubilant.Juju) -> Iterator[None]:
    """Temporarily run update-status hooks every 10 seconds."""
    current_config = juju.model_config()
    previous_interval = current_config.get("update-status-hook-interval", "5m")
    juju.model_config({"update-status-hook-interval": "10s"})
    try:
        yield
    finally:
        juju.model_config({"update-status-hook-interval": previous_interval})


def wait_for_apps(
    juju: jubilant.Juju,
    *apps: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> None:
    """Wait for *apps* to reach an active state."""
    juju.wait(
        lambda status: jubilant.all_active(status, *apps),
        error=jubilant.any_error,
        timeout=timeout,
    )


def first_unit_name(status: jubilant.Status, app: str) -> str:
    """Return the first unit name for *app*."""
    units = status.apps[app].units
    if not units:
        raise AssertionError(f"{app} has no units")
    return next(iter(units))


def ensure_loop_osd(
    juju: jubilant.Juju,
    app: str,
    loop_spec: str,
    unit_names: Iterable[str] | None = None,
) -> None:
    """Ensure each unit listed has a loop-backed OSD enrolled."""
    status = juju.status()
    units = status.apps[app].units
    if not units:
        raise AssertionError(f"{app} has no units to enroll OSDs on")

    targets = list(unit_names) if unit_names else list(units.keys())

    missing_units = [unit for unit in targets if unit not in units]
    if missing_units:
        raise AssertionError(f"Unknown units requested for OSD enrollment: {missing_units}")

    for unit_name in targets:
        task = juju.run(
            unit_name,
            "add-osd",
            {"loop-spec": loop_spec},
            wait=DEFAULT_TIMEOUT,
        )
        task.raise_on_failure()

    with fast_forward(juju):
        wait_for_apps(juju, app, timeout=DEFAULT_TIMEOUT)


def wait_for_broker_response(
    juju: jubilant.Juju,
    requirer_app: str,
    provider_app: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    interval: int = 10,
) -> tuple[dict[str, str], str]:
    """Poll relation data until the requirer broker response is present."""
    deadline = time.time() + timeout
    last_data: dict[str, str] = {}
    broker_rsp_key = ""

    while time.time() < deadline:
        status = juju.status()
        model_name = status.model.name
        requirer_unit = first_unit_name(status, requirer_app)
        provider_unit = first_unit_name(status, provider_app)
        broker_rsp_key = f"broker-rsp-{requirer_unit.replace('/', '-')}"
        last_data = _get_relation_data(requirer_unit, provider_unit, model_name)
        if broker_rsp_key in last_data:
            return last_data, broker_rsp_key
        time.sleep(interval)

    raise AssertionError(
        f"Timed out waiting for broker response ({broker_rsp_key!r}); last data: {last_data}"
    )


def _get_relation_data(requirer_unit: str, provider_unit: str, model_name: str) -> dict[str, str]:
    """Fetch relation data between requirer and provider units."""
    from . import test_utils  # Local import to avoid circular dependencies

    return test_utils.get_relation_data(requirer_unit, "ceph", provider_unit, model_name)


def fetch_ceph_status(juju: jubilant.Juju, app: str) -> dict[str, Any]:
    """Return the output of ``ceph status`` (JSON) from the lead unit."""
    unit_name = first_unit_name(juju.status(), app)
    output = juju.ssh(unit_name, "sudo", "ceph", "status", "--format", "json")
    return json.loads(output)


def assert_osd_count(
    juju: jubilant.Juju,
    app: str,
    *,
    expected_osds: int,
    timeout: int = DEFAULT_TIMEOUT,
    interval: int = 15,
) -> dict[str, Any]:
    """Assert the Ceph cluster exposes *expected_osds* across total/up/in metrics."""

    @retry(stop=stop_after_delay(timeout), wait=wait_fixed(interval), reraise=True)
    def _check() -> dict[str, Any]:
        status = fetch_ceph_status(juju, app)
        raw_osdmap = status.get("osdmap") or {}
        osdmap = raw_osdmap.get("osdmap") if isinstance(raw_osdmap, dict) else {}
        if not osdmap:
            osdmap = raw_osdmap
        num_total = osdmap.get("num_osds")
        num_up = osdmap.get("num_up_osds")
        num_in = osdmap.get("num_in_osds")
        if num_total != expected_osds:
            raise AssertionError(f"Expected {expected_osds} OSDs total, got {num_total}")
        if num_up != expected_osds:
            raise AssertionError(f"Expected {expected_osds} OSDs up, got {num_up}")
        if num_in != expected_osds:
            raise AssertionError(f"Expected {expected_osds} OSDs in, got {num_in}")
        return status

    return _check()
