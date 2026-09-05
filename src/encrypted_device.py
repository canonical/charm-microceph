# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helpers for the OS116 encrypted-device relation contract."""

import glob
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass

DEVICE_REQUESTS_KEY = "device_requests"
DEVICE_RESULTS_KEY = "device_results"


@dataclass(frozen=True)
class DeviceResult:
    """A successfully provisioned device published by Vaultlocker."""

    mapper_path: str
    luks_uuid: str


@dataclass(frozen=True)
class StableDevice:
    """A validated block device identified by a stable /dev/disk/by-id path."""

    path: str
    rdev: int


def resolve_stable_block_device(device_path: str) -> StableDevice:
    """Validate a stable block-device path from Juju storage metadata."""
    device_stat = os.stat(device_path)
    if not stat.S_ISBLK(device_stat.st_mode):
        raise ValueError(f"{device_path} is not a block device")

    if device_path.startswith("/dev/disk/by-id/"):
        return StableDevice(path=device_path, rdev=device_stat.st_rdev)

    for stable_path in sorted(glob.glob("/dev/disk/by-id/*")):
        try:
            stable_stat = os.stat(stable_path)
        except OSError:
            continue
        if stat.S_ISBLK(stable_stat.st_mode) and stable_stat.st_rdev == device_stat.st_rdev:
            return StableDevice(path=stable_path, rdev=device_stat.st_rdev)

    raise ValueError(f"No stable /dev/disk/by-id path found for {device_path}")


def validate_fresh_encryption_target(device_path: str) -> None:
    """Reject a device that is already mounted or used as a lower layer."""
    target_path = os.path.realpath(device_path)
    target = _find_block_device(_lsblk_block_devices(), target_path)
    if target is None:
        raise ValueError("could not find device in lsblk output")
    if any(target.get("mountpoints") or []):
        raise ValueError("device is mounted")
    if target.get("children"):
        raise ValueError("device is used as a lower block-device layer")
    root_source = _root_source()
    if root_source and os.path.realpath(root_source) == target_path:
        raise ValueError("device backs the root filesystem")
    if any(os.path.realpath(source) == target_path for source in _swap_sources()):
        raise ValueError("device is used as swap")


def _lsblk_block_devices() -> list[dict]:
    """Return lsblk's nested device tree or report an unusable host inspection."""
    try:
        process = subprocess.run(
            ["lsblk", "--json", "--output", "NAME,PATH,MOUNTPOINTS"],
            capture_output=True,
            check=True,
            text=True,
        )
        return json.loads(process.stdout)["blockdevices"]
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as exc:
        raise ValueError("could not inspect block-device usage") from exc


def _root_source() -> str:
    """Return the source backing the root filesystem."""
    try:
        return subprocess.run(
            ["findmnt", "--noheadings", "--output", "SOURCE", "/"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("could not inspect root filesystem") from exc


def _swap_sources() -> list[str]:
    """Return the paths registered as swap devices."""
    try:
        with open("/proc/swaps", encoding="utf-8") as swaps:
            return [line.split()[0] for line in swaps.readlines()[1:] if line.split()]
    except OSError as exc:
        raise ValueError("could not inspect swap devices") from exc


def _find_block_device(blockdevices: list[dict], target_path: str) -> dict | None:
    """Find a device by its resolved path in nested lsblk JSON output."""
    for device in blockdevices:
        path = device.get("path")
        if path and os.path.realpath(path) == target_path:
            return device
        found = _find_block_device(device.get("children") or [], target_path)
        if found is not None:
            return found
    return None


def validate_mapper_block_device(mapper_path: str) -> None:
    """Confirm that a provider result names an available device-mapper block node."""
    if not mapper_path.startswith("/dev/mapper/"):
        raise ValueError("mapper_path is not under /dev/mapper")

    try:
        mapper_stat = os.stat(mapper_path)
    except OSError as exc:
        raise ValueError("mapper_path is not available") from exc

    if not stat.S_ISBLK(mapper_stat.st_mode):
        raise ValueError("mapper_path is not a block device")


def render_osd_unlock_dropin(luks_uuids: list[str]) -> str:
    """Render OSD systemd ordering for Vaultlocker-managed mapper devices.

    Vaultlocker's decrypt units are one-shot services, so they are deliberately
    ordered with ``After=`` rather than made ``Requires=`` dependencies.
    """
    unlock_units = [f"vaultlocker-decrypt@{uuid}.service" for uuid in sorted(luks_uuids)]
    unit_list = " ".join(unlock_units)
    return "# Managed by charm-microceph. Do not edit.\n" "[Unit]\n" f"After={unit_list}\n"


def build_fresh_device_requests(device_paths: list[str]) -> str:
    """Encode fresh-encryption requests for the encrypted-device relation."""
    return json.dumps({path: {} for path in sorted(device_paths)}, sort_keys=True)


def parse_device_results(raw_results: str) -> dict[str, DeviceResult]:
    """Decode successful encrypted-device results from a provider unit databag."""
    try:
        encoded_results = json.loads(raw_results)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("device_results is not valid JSON") from exc

    if not isinstance(encoded_results, dict):
        raise ValueError("device_results must be a JSON object")

    results = {}
    for path, result in encoded_results.items():
        if not isinstance(path, str) or not isinstance(result, dict):
            raise ValueError("device_results entries must map paths to objects")

        mapper_path = result.get("mapper_path")
        if not isinstance(mapper_path, str) or not mapper_path:
            raise ValueError("device_results entry is missing mapper_path")
        if not mapper_path.startswith("/dev/mapper/"):
            raise ValueError("device_results entry has an invalid mapper_path")

        luks_uuid = result.get("luks_uuid")
        if not isinstance(luks_uuid, str) or not luks_uuid:
            raise ValueError("device_results entry is missing luks_uuid")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", luks_uuid):
            raise ValueError("device_results entry has an invalid luks_uuid")

        results[path] = DeviceResult(mapper_path=mapper_path, luks_uuid=luks_uuid)

    return results
