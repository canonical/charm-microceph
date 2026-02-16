"""Shared helper functions for integration tests."""

import contextlib
import functools
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping
from urllib import request

import jubilant
import yaml
from tenacity import retry, stop_after_delay, wait_fixed

CEPHTOOLS_URL = "https://github.com/canonical/cephtools/releases/download/latest/cephtools"
CEPHTOOLS_PATH = Path("/usr/local/bin/cephtools")
DEFAULT_TIMEOUT = 1200

logger = logging.getLogger(__name__)


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


def deploy_microceph(
    juju: jubilant.Juju,
    charm_path: Path | str,
    app: str,
    *,
    config: Mapping[str, Any] | None = None,
    loop_osd_spec: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Deploy a MicroCeph app and optionally enroll loop-backed OSDs."""
    if config:
        juju.deploy(str(charm_path), app, config=dict(config))
    else:
        juju.deploy(str(charm_path), app)

    with fast_forward(juju):
        wait_for_apps(juju, app, timeout=timeout)

    if loop_osd_spec:
        ensure_loop_osd(juju, app, loop_osd_spec)

    return app


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


def ceph_health_matches(
    status: dict[str, Any], *, allowed_warn_checks: set[str] | None = None
) -> bool:
    """Return whether Ceph status is healthy for test expectations.

    A status is considered healthy if either:
    - ``health.status`` is ``HEALTH_OK``; or
    - ``health.status`` is ``HEALTH_WARN`` and all reported check names are in
      ``allowed_warn_checks``.
    """
    health = status.get("health", {})
    current = health.get("status")
    if current == "HEALTH_OK":
        return True

    if current != "HEALTH_WARN" or not allowed_warn_checks:
        return False

    checks = health.get("checks")
    if not isinstance(checks, dict) or not checks:
        return False

    return set(checks).issubset(allowed_warn_checks)


def ceph_health_mismatch_reason(
    status: dict[str, Any], *, allowed_warn_checks: set[str] | None = None
) -> str:
    """Return a human-readable reason when Ceph health is not acceptable."""
    health = status.get("health", {})
    current = health.get("status")
    checks = health.get("checks")
    check_names = sorted(checks.keys()) if isinstance(checks, dict) else []

    if current == "HEALTH_OK":
        return "health is HEALTH_OK"

    if current != "HEALTH_WARN":
        if allowed_warn_checks:
            return (
                f"health is {current!r}; expected HEALTH_OK or HEALTH_WARN with checks "
                f"subset of {sorted(allowed_warn_checks)}"
            )
        return f"health is {current!r}; expected HEALTH_OK"

    if not allowed_warn_checks:
        return "health is HEALTH_WARN and no warn checks are allowed " f"(checks: {check_names})"

    if not isinstance(checks, dict) or not checks:
        return (
            "health is HEALTH_WARN but no checks were reported; "
            f"allowed checks: {sorted(allowed_warn_checks)}"
        )

    disallowed = sorted(set(checks) - allowed_warn_checks)
    if disallowed:
        return (
            f"health is HEALTH_WARN with disallowed checks {disallowed}; "
            f"all checks: {check_names}; allowed checks: {sorted(allowed_warn_checks)}"
        )

    return (
        f"health is HEALTH_WARN with checks {check_names}, all allowed by policy "
        f"{sorted(allowed_warn_checks)}"
    )


def wait_for_ceph_health_ok(
    juju: jubilant.Juju,
    app: str,
    timeout: int = DEFAULT_TIMEOUT,
    *,
    allowed_warn_checks: set[str] | None = None,
) -> dict[str, Any]:
    """Wait until ``ceph status`` for *app* is acceptable for tests.

    By default this requires strict ``HEALTH_OK``.
    Optionally, callers can allow specific warning checks via
    ``allowed_warn_checks``.
    """
    deadline = time.time() + timeout
    last_status: dict[str, Any] = {}
    while time.time() < deadline:
        last_status = fetch_ceph_status(juju, app)
        if ceph_health_matches(last_status, allowed_warn_checks=allowed_warn_checks):
            return last_status
        time.sleep(15)

    allowed_msg = (
        ""
        if not allowed_warn_checks
        else f" (allowing HEALTH_WARN checks: {sorted(allowed_warn_checks)})"
    )
    reason = ceph_health_mismatch_reason(
        last_status,
        allowed_warn_checks=allowed_warn_checks,
    )
    raise AssertionError(
        "Ceph health did not become acceptable"
        f"{allowed_msg}; last evaluation: {reason}; last status: {last_status}"
    )


def _guess_pool_application(pool_name: str) -> str:
    """Guess a sensible ceph pool application for a pool name."""
    normalized = pool_name.lstrip(".").lower()
    if normalized == "mgr":
        return "mgr"
    if "rgw" in normalized:
        return "rgw"
    if "cephfs" in normalized or "mds" in normalized:
        return "cephfs"
    return "rbd"


def enable_missing_pool_apps(
    juju: jubilant.Juju,
    app: str,
    *,
    unit_name: str | None = None,
) -> None:
    """Enable pool application metadata for pools that do not have one."""
    if unit_name is None:
        unit_name = first_unit_name(juju.status(), app)

    output = juju.ssh(
        unit_name,
        "sudo",
        "ceph",
        "osd",
        "pool",
        "ls",
        "detail",
        "--format",
        "json",
    )
    pools = json.loads(output)

    for pool in pools:
        pool_name = pool.get("pool_name")
        if not pool_name:
            continue

        app_metadata = pool.get("application_metadata") or {}
        if app_metadata:
            continue

        app_name = _guess_pool_application(pool_name)
        logger.info("Enabling app %s on ceph pool %s", app_name, pool_name)
        juju.ssh(
            unit_name,
            "sudo",
            "ceph",
            "osd",
            "pool",
            "application",
            "enable",
            pool_name,
            app_name,
        )


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
