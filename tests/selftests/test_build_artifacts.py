# Copyright 2026 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for the native build matrix and release artifact validation."""

from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from tests.scripts.check_charm_artifacts import ELF_TARGETS, main, validate_artifacts

ROOT = Path(__file__).resolve().parents[2]


def _elf(arch, *, encoding=None, bits=2):
    machine, expected_encoding = ELF_TARGETS[arch]
    encoding = expected_encoding if encoding is None else encoding
    return (
        b"\x7fELF"
        + bytes([bits, encoding])
        + bytes(12)
        + machine.to_bytes(2, "little" if encoding == 1 else "big")
    )


def _charm(directory, base="26.04", arch="amd64", *, manifest_base=None, binary=None):
    path = directory / f"microceph_ubuntu-{base}-{arch}.charm"
    manifest = {
        "bases": [{"name": "ubuntu", "channel": manifest_base or base, "architectures": [arch]}]
    }
    with ZipFile(path, "w") as charm:
        charm.writestr("manifest.yaml", yaml.safe_dump(manifest))
        charm.writestr("venv/lib/python3.14/site-packages/module.so", binary or _elf(arch))
    return path


@pytest.fixture
def artifacts(tmp_path):
    config = yaml.safe_load((ROOT / "charmcraft.yaml").read_text())
    for definition in config["platforms"].values():
        base, arch = definition["build-for"][0].removeprefix("ubuntu@").split(":")
        _charm(tmp_path, base, arch)
    return tmp_path


def test_complete_native_artifact_set(artifacts):
    paths = validate_artifacts(artifacts, ROOT / "charmcraft.yaml")
    assert len(paths) == 5
    assert paths == sorted(artifacts.glob("*.charm"))


@pytest.mark.parametrize("arch", ELF_TARGETS)
def test_missing_artifact_blocks_release(artifacts, arch):
    (artifacts / f"microceph_ubuntu-26.04-{arch}.charm").unlink()
    with pytest.raises(ValueError, match="missing=.*26.04-" + arch):
        validate_artifacts(artifacts, ROOT / "charmcraft.yaml")


def test_empty_artifact_set_blocks_release(tmp_path):
    with pytest.raises(ValueError, match="missing="):
        validate_artifacts(tmp_path, ROOT / "charmcraft.yaml")


def test_unexpected_artifact_blocks_release(artifacts):
    _charm(artifacts, base="24.04", arch="arm64")
    with pytest.raises(ValueError, match="unexpected=.*24.04-arm64"):
        validate_artifacts(artifacts, ROOT / "charmcraft.yaml")


def test_incorrect_manifest_base_blocks_release(artifacts):
    _charm(artifacts, arch="arm64", manifest_base="24.04")
    with pytest.raises(ValueError, match="expected bases"):
        validate_artifacts(artifacts, ROOT / "charmcraft.yaml")


@pytest.mark.parametrize("arch", ["arm64", "s390x", "ppc64el"])
def test_host_architecture_wheels_block_release(artifacts, arch):
    _charm(artifacts, arch=arch, binary=_elf("amd64"))
    with pytest.raises(ValueError, match=f"not a {arch} ELF binary"):
        validate_artifacts(artifacts, ROOT / "charmcraft.yaml")


@pytest.mark.parametrize("arch,encoding", [("s390x", 1), ("ppc64el", 2)])
def test_wrong_endianness_blocks_release(artifacts, arch, encoding):
    _charm(artifacts, arch=arch, binary=_elf(arch, encoding=encoding))
    with pytest.raises(ValueError, match=f"not a {arch} ELF binary"):
        validate_artifacts(artifacts, ROOT / "charmcraft.yaml")


def test_wrong_elf_class_blocks_release(artifacts):
    _charm(artifacts, binary=_elf("amd64", bits=1))
    with pytest.raises(ValueError, match="not a amd64 ELF binary"):
        validate_artifacts(artifacts, ROOT / "charmcraft.yaml")


def test_missing_native_dependencies_blocks_release(artifacts):
    _charm(artifacts, binary=b"not an ELF file")
    with pytest.raises(ValueError, match="no native dependencies"):
        validate_artifacts(artifacts, ROOT / "charmcraft.yaml")


def test_cli_emits_only_comma_separated_artifact_paths(artifacts, monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr("sys.argv", ["check_charm_artifacts.py", str(artifacts)])
    main()
    output = capsys.readouterr()
    assert output.out.strip().split(",") == [
        str(path) for path in sorted(artifacts.glob("*.charm"))
    ]
    assert not output.err


def test_ci_matrix_matches_native_platforms():
    config = yaml.safe_load((ROOT / "charmcraft.yaml").read_text())
    workflow = yaml.safe_load((ROOT / ".github/workflows/build.yaml").read_text())
    matrix = workflow["jobs"]["pack"]["strategy"]["matrix"]["include"]
    platforms = config["platforms"]
    assert len(matrix) == len(platforms) == 5
    assert {entry["platform"] for entry in matrix} == set(platforms)
    runners = {
        "amd64": "ubuntu-24.04",
        "arm64": "self-hosted-linux-arm64-resolute-xlarge",
        "s390x": "self-hosted-linux-s390x-resolute-medium",
        "ppc64el": "self-hosted-linux-ppc64el-resolute-medium",
    }
    for entry in matrix:
        definition = platforms[entry["platform"]]
        assert definition["build-on"] == definition["build-for"]
        target = definition["build-for"][0]
        arch = target.split(":")[1]
        assert entry["runner"] == runners[arch]
        if arch != "amd64":
            assert target.startswith("ubuntu@26.04:")


def test_tests_and_release_share_validated_build():
    for name in ["build-and-test.yml", "main.yaml"]:
        workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
        assert workflow["jobs"]["build"]["uses"] == "./.github/workflows/build.yaml"
    assert "build" in workflow["jobs"]["release-to-charmhub"]["needs"]
