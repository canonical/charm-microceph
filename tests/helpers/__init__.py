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
import yaml
from tenacity import retry, stop_after_delay, wait_fixed

CEPHTOOLS_URL = "https://github.com/canonical/cephtools/releases/download/latest/cephtools"
CEPHTOOLS_PATH = Path("/usr/local/bin/cephtools")
DEFAULT_TIMEOUT = 1200


@functools.lru_cache(maxsize=1)
def install_terraform_tooling() -> None:
    """Install Terraform and Terragrunt via the cephtools helper."""
    cephtools_path = _ensure_cephtools_binary()
    subprocess.run(["sudo", str(cephtools_path), "terraform", "install-deps"], check=True)


@functools.lru_cache(maxsize=1)
def ensure_charmcraft() -> None:
    """Install charmcraft snap if it is not already available."""
    if shutil.which("charmcraft"):
        return
    subprocess.run(["sudo", "snap", "install", "charmcraft", "--classic"], check=True)


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


def wait_for_status(
    juju: jubilant.Juju,
    app: str,
    statuses: tuple[str, ...],
    timeout: int = DEFAULT_TIMEOUT,
) -> None:
    """Wait for *app* to reach one of the given *statuses*."""

    def _check(status: jubilant.Status) -> bool:
        app_status = status.apps.get(app)
        if not app_status:
            return False
        return app_status.app_status.current in statuses

    juju.wait(_check, timeout=timeout)


def wait_for_microceph_units(
    juju: jubilant.Juju, app: str, *, expected_units: int, timeout: int = DEFAULT_TIMEOUT
) -> None:
    """Wait until *app* reports at least *expected_units* units."""

    def _has_units(status: jubilant.Status) -> bool:
        app_status = status.apps.get(app)
        if not app_status or not app_status.units:
            return False
        return len(app_status.units) >= expected_units

    juju.wait(_has_units, error=jubilant.any_error, timeout=timeout)


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


def get_unit_info(unit: str, model: str) -> dict[str, Any]:
    """Return ``juju show-unit`` data for a specific unit."""
    proc = subprocess.run(
        ["juju", "show-unit", "-m", model, unit],
        check=True,
        capture_output=True,
        text=True,
    )
    raw_data = proc.stdout.strip()
    data = yaml.safe_load(raw_data) if raw_data else None

    if not data:
        raise ValueError(f"No unit info available for {unit}")

    if unit not in data:
        raise KeyError(unit, f"not in {data!r}")

    return data[unit]


def get_relation_data(unit: str, endpoint: str, related_unit: str, model: str) -> dict[str, str]:
    """Return relation data for a local endpoint and remote unit."""
    unit_data = get_unit_info(unit, model)
    for relation in unit_data.get("relation-info", []):
        related_units = relation.get("related-units", {})
        if endpoint == relation.get("endpoint") and related_unit in related_units:
            return related_units.get(related_unit).get("data")

    return {}


def _get_relation_data(requirer_unit: str, provider_unit: str, model_name: str) -> dict[str, str]:
    """Fetch relation data between requirer and provider units."""
    return get_relation_data(requirer_unit, "ceph", provider_unit, model_name)


def fetch_ceph_status(juju: jubilant.Juju, app: str) -> dict[str, Any]:
    """Return the output of ``ceph status`` (JSON) from the lead unit."""
    unit_name = first_unit_name(juju.status(), app)
    output = juju.ssh(unit_name, "sudo", "ceph", "status", "--format", "json")
    return json.loads(output)


def wait_for_ceph_health_ok(
    juju: jubilant.Juju, app: str, timeout: int = DEFAULT_TIMEOUT
) -> dict[str, Any]:
    """Wait until ``ceph status`` for *app* reports ``HEALTH_OK``."""
    deadline = time.time() + timeout
    last_status: dict[str, Any] = {}
    while time.time() < deadline:
        last_status = fetch_ceph_status(juju, app)
        health = last_status.get("health", {})
        if health.get("status") == "HEALTH_OK":
            return last_status
        time.sleep(15)
    raise AssertionError(f"Ceph health did not reach HEALTH_OK; last status: {last_status}")


def exercise_rgw(juju: jubilant.Juju, unit_name: str, filename: str = "test") -> None:
    """Create an RGW user, upload, and fetch an object via s3cmd on unit_name."""
    script = (
        "set -euo pipefail\n"
        f'filename="{filename}"\n'
        "sudo apt-get -qq -y install s3cmd || true\n"
        "if ! sudo microceph.radosgw-admin user list | grep -q test; then\n"
        '  echo "Create S3 user: test"\n'
        "  sudo microceph.radosgw-admin user create --uid=test --display-name=test\n"
        "  sudo microceph.radosgw-admin key create --uid=test --key-type=s3 "
        "--access-key fooAccessKey --secret-key fooSecretKey\n"
        "fi\n"
        "tmpfile=$(mktemp /tmp/rgw-object.XXXXXX)\n"
        'echo hello-radosgw > "${tmpfile}"\n'
        's3_args=(--host localhost --host-bucket="localhost/%(bucket)" '
        "--access_key=fooAccessKey --secret_key=fooSecretKey --no-ssl)\n"
        's3cmd "${s3_args[@]}" mb s3://testbucket || true\n'
        's3cmd "${s3_args[@]}" put -P "${tmpfile}" s3://testbucket/"${filename}.txt"\n'
        'curl -s http://localhost/testbucket/"${filename}.txt" | grep -F hello-radosgw\n'
    )
    juju.ssh(unit_name, "bash", "-c", script)


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


def find_repo_root(start: Path, dirname: str = "terraform") -> Path:
    """Locate repository root by walking upward until a dir is found."""
    for path in (start, *start.parents):
        if (path / dirname).is_dir():
            return path
    raise FileNotFoundError("Could not locate repository root containing directory")
