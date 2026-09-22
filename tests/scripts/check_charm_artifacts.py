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

"""Validate the complete native build set before testing or publishing it."""

import argparse
from pathlib import Path
from zipfile import ZipFile

import yaml

# ELF e_machine and EI_DATA. In particular, s390x is big-endian and ppc64el is not.
ELF_TARGETS = {"amd64": (62, 1), "arm64": (183, 1), "s390x": (22, 2), "ppc64el": (21, 1)}


def _validate_charm(path: Path, target: str) -> None:
    """Check manifest targeting and every ELF member, not just wheel filenames."""
    base, arch = target.split(":")
    name, channel = base.split("@")
    expected_bases = [{"name": name, "channel": channel, "architectures": [arch]}]
    machine, encoding = ELF_TARGETS[arch]
    native_count = 0
    with ZipFile(path) as charm:
        manifest = yaml.safe_load(charm.read("manifest.yaml"))
        if manifest.get("bases") != expected_bases:
            raise ValueError(
                f"{path}: expected bases {expected_bases}, got {manifest.get('bases')}"
            )
        for member in charm.infolist():
            with charm.open(member) as stream:
                header = stream.read(20)
            if not header.startswith(b"\x7fELF"):
                continue
            native_count += 1
            if (
                len(header) < 20
                or header[4] != 2  # ELFCLASS64
                or header[5] != encoding
                or int.from_bytes(header[18:20], "little" if encoding == 1 else "big") != machine
            ):
                raise ValueError(f"{path}: {member.filename} is not a {arch} ELF binary")
    if not native_count:
        raise ValueError(f"{path}: no native dependencies found")


def validate_artifacts(directory: Path, config_path: Path = Path("charmcraft.yaml")) -> list[Path]:
    """Return ordered artifact paths only if every configured native platform is present."""
    config = yaml.safe_load(config_path.read_text())
    platforms = config["platforms"]
    expected = {f"microceph_{platform}.charm" for platform in platforms}
    actual = {path.name for path in directory.glob("*.charm")}
    if actual != expected:
        raise ValueError(
            f"Incomplete or unexpected artifact set: missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}"
        )
    artifacts = []
    for platform, definition in sorted(platforms.items()):
        targets = definition["build-for"]
        if len(targets) != 1 or definition["build-on"] != targets:
            raise ValueError(f"{platform}: expected one native build-on/build-for target")
        path = directory / f"microceph_{platform}.charm"
        _validate_charm(path, targets[0])
        artifacts.append(path)
    return artifacts


def main() -> None:
    """Print comma-separated validated paths for upload-charm's built-charm-path input."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(",".join(str(path) for path in validate_artifacts(args.directory)))


if __name__ == "__main__":
    main()
