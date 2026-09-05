# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Tests for the encrypted-device relation contract."""

import json
import stat
from types import SimpleNamespace
from unittest.mock import mock_open, patch

import pytest

from encrypted_device import (
    StableDevice,
    build_fresh_device_requests,
    parse_device_results,
    render_osd_unlock_dropin,
    resolve_stable_block_device,
    validate_fresh_encryption_target,
    validate_mapper_block_device,
)


def test_resolve_stable_block_device_preserves_by_id_path():
    """A stable Juju attachment path is retained after block-device validation."""
    path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"
    with patch(
        "encrypted_device.os.stat",
        return_value=SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=2048),
    ):
        device = resolve_stable_block_device(path)

    assert device == StableDevice(path=path, rdev=2048)


def test_resolve_stable_block_device_finds_matching_by_id_path():
    """A non-stable Juju path is replaced with a matching by-id symlink."""
    attachment_path = "/dev/vdb"
    stable_path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"
    device_stat = SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=2048)

    with (
        patch("encrypted_device.glob.glob", return_value=[stable_path]),
        patch("encrypted_device.os.stat", return_value=device_stat),
    ):
        device = resolve_stable_block_device(attachment_path)

    assert device == StableDevice(path=stable_path, rdev=2048)


def test_validate_fresh_encryption_target_rejects_mounted_device():
    """A requesting charm never asks Vaultlocker to encrypt a mounted device."""
    path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"
    lsblk_output = json.dumps(
        {
            "blockdevices": [
                {
                    "path": path,
                    "mountpoints": ["/data"],
                }
            ]
        }
    )
    process = SimpleNamespace(stdout=lsblk_output)

    with patch("encrypted_device.subprocess.run", return_value=process):
        with pytest.raises(ValueError, match="mounted"):
            validate_fresh_encryption_target(path)


def test_validate_fresh_encryption_target_rejects_lower_block_device_layer():
    """A device with consumers is never offered for fresh encryption."""
    path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"
    lsblk_output = json.dumps(
        {
            "blockdevices": [
                {
                    "path": path,
                    "mountpoints": [None],
                    "children": [{"path": "/dev/mapper/used-by-lvm", "mountpoints": [None]}],
                }
            ]
        }
    )

    def run(command, **_kwargs):
        if command[0] == "lsblk":
            return SimpleNamespace(stdout=lsblk_output)
        return SimpleNamespace(stdout="/dev/vda")

    with (
        patch("encrypted_device.subprocess.run", side_effect=run),
        patch("builtins.open", mock_open(read_data="Filename\tType\tSize\tUsed\tPriority\n")),
    ):
        with pytest.raises(ValueError, match="lower block-device layer"):
            validate_fresh_encryption_target(path)


def test_validate_fresh_encryption_target_rejects_root_filesystem_device():
    """The root block device is never offered for destructive encryption."""
    path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"
    lsblk_output = json.dumps({"blockdevices": [{"path": path, "mountpoints": [None]}]})

    def run(command, **_kwargs):
        if command[0] == "lsblk":
            return SimpleNamespace(stdout=lsblk_output)
        return SimpleNamespace(stdout=path)

    with patch("encrypted_device.subprocess.run", side_effect=run):
        with pytest.raises(ValueError, match="root filesystem"):
            validate_fresh_encryption_target(path)


def test_validate_fresh_encryption_target_rejects_swap_device():
    """A swap device is never offered for destructive encryption."""
    path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"
    lsblk_output = json.dumps({"blockdevices": [{"path": path, "mountpoints": [None]}]})

    def run(command, **_kwargs):
        if command[0] == "lsblk":
            return SimpleNamespace(stdout=lsblk_output)
        return SimpleNamespace(stdout="/dev/vda")

    swaps = "Filename\tType\tSize\tUsed\tPriority\n{}\tpartition\t1\t0\t-2\n".format(path)
    with (
        patch("encrypted_device.subprocess.run", side_effect=run),
        patch("builtins.open", mock_open(read_data=swaps)),
    ):
        with pytest.raises(ValueError, match="swap"):
            validate_fresh_encryption_target(path)


def test_validate_fresh_target_requests_lsblk_tree_output():
    """Lower-layer detection requires NAME to preserve lsblk's device tree."""
    path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"
    commands = []
    lsblk_output = json.dumps({"blockdevices": [{"path": path, "mountpoints": [None]}]})

    def run(command, **_kwargs):
        commands.append(command)
        if command[0] == "lsblk":
            return SimpleNamespace(stdout=lsblk_output)
        return SimpleNamespace(stdout="/dev/vda")

    with (
        patch("encrypted_device.subprocess.run", side_effect=run),
        patch("builtins.open", mock_open(read_data="Filename\tType\tSize\tUsed\tPriority\n")),
    ):
        validate_fresh_encryption_target(path)

    assert commands[0] == ["lsblk", "--json", "--output", "NAME,PATH,MOUNTPOINTS"]


def test_validate_mapper_block_device_requires_an_open_block_device():
    """A result is consumed only while its mapper is a block device."""
    mapper_path = "/dev/mapper/crypt-a1b2c3d4"
    with patch(
        "encrypted_device.os.stat",
        return_value=SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=253),
    ):
        validate_mapper_block_device(mapper_path)


def test_render_osd_unlock_dropin_orders_each_vaultlocker_unit():
    """The MicroCeph OSD service waits for all managed unlock units at boot."""
    assert render_osd_unlock_dropin(["b2", "a1"]) == (
        "# Managed by charm-microceph. Do not edit.\n"
        "[Unit]\n"
        "After=vaultlocker-decrypt@a1.service vaultlocker-decrypt@b2.service\n"
    )


def test_build_fresh_device_requests_uses_empty_request_values():
    """Fresh-encryption requests use stable paths as keys and empty values."""
    requests = build_fresh_device_requests(
        [
            "/dev/disk/by-id/wwn-0x5000c500aabbcc01",
            "/dev/disk/by-id/wwn-0x5000c500aabbcc02",
        ]
    )

    assert json.loads(requests) == {
        "/dev/disk/by-id/wwn-0x5000c500aabbcc01": {},
        "/dev/disk/by-id/wwn-0x5000c500aabbcc02": {},
    }


def test_parse_device_results_returns_mapper_and_luks_uuid():
    """A completed provider result supplies the mapper path and LUKS UUID."""
    results = parse_device_results(
        json.dumps(
            {
                "/dev/disk/by-id/wwn-0x5000c500aabbcc01": {
                    "mapper_path": "/dev/mapper/crypt-a1b2c3d4",
                    "luks_uuid": "a1b2c3d4",
                }
            }
        )
    )

    result = results["/dev/disk/by-id/wwn-0x5000c500aabbcc01"]
    assert result.mapper_path == "/dev/mapper/crypt-a1b2c3d4"
    assert result.luks_uuid == "a1b2c3d4"


def test_parse_device_results_rejects_non_mapper_path():
    """Provider results must identify the opened device-mapper node."""
    with pytest.raises(ValueError, match="mapper_path"):
        parse_device_results(
            json.dumps(
                {
                    "/dev/disk/by-id/wwn-0x5000c500aabbcc01": {
                        "mapper_path": "/dev/vdb",
                        "luks_uuid": "a1b2c3d4",
                    }
                }
            )
        )


def test_parse_device_results_rejects_unsafe_luks_uuid():
    """A LUKS UUID cannot inject additional systemd unit directives."""
    with pytest.raises(ValueError, match="luks_uuid"):
        parse_device_results(
            json.dumps(
                {
                    "/dev/disk/by-id/wwn-0x5000c500aabbcc01": {
                        "mapper_path": "/dev/mapper/crypt-a1b2c3d4",
                        "luks_uuid": "a1\nRequires=attacker.service",
                    }
                }
            )
        )


def test_parse_device_results_rejects_incomplete_provider_result():
    """An incomplete provider result is not safe to consume as an OSD."""
    with pytest.raises(ValueError, match="mapper_path"):
        parse_device_results(
            json.dumps(
                {
                    "/dev/disk/by-id/wwn-0x5000c500aabbcc01": {
                        "luks_uuid": "a1b2c3d4",
                    }
                }
            )
        )
