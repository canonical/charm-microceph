# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Host-side validation helpers for Vaultlocker-backed devices."""

import glob
import json
import os
import stat
import subprocess
from dataclasses import dataclass


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
