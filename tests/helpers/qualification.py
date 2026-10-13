"""Optional installed-snap identity checks for ceph-qa release qualification."""

import hashlib
import json
from pathlib import Path

# Executed by ordinary Juju SSH, never by manually invoking a charm hook.
IDENTITY_PROBE = r"""
import hashlib, json, subprocess
from pathlib import Path
rows = subprocess.run(["snap", "list", "microceph"], check=True,
                      capture_output=True, text=True).stdout.splitlines()
if len(rows) != 2:
    raise RuntimeError("Unexpected snap list result")
fields = rows[1].split()
rev = int(fields[2])
sha = hashlib.sha3_384()
with Path(f"/var/lib/snapd/snaps/microceph_{rev}.snap").open("rb") as binary:
    for block in iter(lambda: binary.read(1024 * 1024), b""):
        sha.update(block)
arch = subprocess.run(["dpkg", "--print-architecture"], check=True,
                      capture_output=True, text=True).stdout.strip()
print(json.dumps(dict(revision=rev, sha3_384=sha.hexdigest(), architecture=arch,
                      held="held" in fields[-1].split(","))))
"""


class Qualification:
    """Record all units' actual identities, failing on drift or incomplete evidence."""

    def __init__(self, manifest: dict, evidence: Path):
        self.manifest = manifest
        self.evidence = Path(evidence)
        if (
            manifest.get("schema") != 1
            or manifest.get("snap") != "microceph"
            or manifest.get("track") != "tentacle"
            or manifest.get("architecture") != "amd64"
        ):
            raise ValueError("Wrong qualification manifest scope")
        self.digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def audit(self, juju, app: str, test: str, boundary: str) -> None:
        """Verify and record identities before/after a qualification test."""
        status = juju.status()
        application = status.apps.get(app)
        units = application.units if application else {}
        if not units:
            if boundary == "before" and test.endswith("::test_initial_single_unit"):
                return  # This test creates the application's first units.
            raise AssertionError("No MicroCeph units available for qualification")
        baseline = (
            test.endswith("::test_upgrade_health_warning_recovers_on_update_status")
            and boundary == "before"
        )
        target = self.manifest["beta" if baseline else "edge"]
        for unit in sorted(units):
            juju.ssh(unit, "sudo", "snap", "refresh", "--hold=forever", "microceph")
            observed = json.loads(juju.ssh(unit, "sudo", "python3", "-c", IDENTITY_PROBE))
            if (
                observed.get("revision") != target["revision"]
                or observed.get("sha3_384") != target["sha3_384"]
                or observed.get("architecture") != "amd64"
                or observed.get("held") is not True
            ):
                raise AssertionError(f"MicroCeph identity mismatch on {unit}: {observed}")
            row = dict(
                observed,
                manifest_sha256=self.digest,
                model=juju.model,
                unit=unit,
                test=test,
                boundary=boundary,
            )
            self.evidence.parent.mkdir(parents=True, exist_ok=True)
            with self.evidence.open("a") as output:
                output.write(json.dumps(row, sort_keys=True) + "\n")


def test_identity(nodeid: str) -> str:
    """Convert a pytest node ID to the JUnit classname/name identity."""
    parts = nodeid.split("::")
    module = parts[0].removesuffix(".py").replace("/", ".")
    return ".".join([module, *parts[1:-1]]) + "::" + parts[-1]
