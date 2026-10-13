"""Tests for optional revision-bound qualification evidence."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE = Path(__file__).parents[1] / "helpers" / "qualification.py"


def implementation():
    assert MODULE.is_file(), "installed-revision qualification checks are not implemented"
    spec = importlib.util.spec_from_file_location("qualification", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest():
    return dict(
        schema=1,
        snap="microceph",
        track="tentacle",
        architecture="amd64",
        source_sha="a" * 40,
        run_id="run-1",
        edge=dict(revision=2051, sha3_384="a" * 96),
        beta=dict(revision=2033, sha3_384="b" * 96),
    )


class Juju:
    model = "model-1"

    def __init__(self, observed, units=None):
        self.observed = observed
        self.units = units if units is not None else {"microceph/0": None}
        self.commands = []

    def status(self):
        return SimpleNamespace(apps={"microceph": SimpleNamespace(units=self.units)})

    def ssh(self, unit, *args):
        self.commands.append((unit, args))
        if "python3" in args:
            return json.dumps(self.observed)
        return ""


def observed(rev=2051):
    return dict(
        revision=rev, architecture="amd64", held=True, sha3_384=("b" if rev == 2033 else "a") * 96
    )


def test_records_actual_identity_at_both_boundaries(tmp_path):
    q = implementation().Qualification(manifest(), tmp_path / "evidence.jsonl")
    juju = Juju(observed(), {"microceph/0": None, "microceph/1": None})
    q.audit(juju, "microceph", "module::test", "before")
    q.audit(juju, "microceph", "module::test", "after")
    rows = [json.loads(line) for line in q.evidence.read_text().splitlines()]
    assert len(rows) == 4
    assert {row["unit"] for row in rows} == {"microceph/0", "microceph/1"}
    assert all(row["revision"] == 2051 for row in rows)
    assert all(row["manifest_sha256"] == q.digest for row in rows)


@pytest.mark.parametrize(
    "key,value",
    [("revision", 2052), ("architecture", "arm64"), ("held", False), ("sha3_384", "c" * 96)],
)
def test_wrong_runtime_identity_rejected(tmp_path, key, value):
    q = implementation().Qualification(manifest(), tmp_path / "evidence.jsonl")
    data = observed()
    data[key] = value
    with pytest.raises(AssertionError):
        q.audit(Juju(data), "microceph", "module::test", "before")


def test_baseline_allowed_only_before_recovery_test(tmp_path):
    q = implementation().Qualification(manifest(), tmp_path / "evidence.jsonl")
    name = "module::test_upgrade_health_warning_recovers_on_update_status"
    q.audit(Juju(observed(2033)), "microceph", name, "before")
    q.audit(Juju(observed()), "microceph", name, "after")
    with pytest.raises(AssertionError):
        q.audit(Juju(observed(2033)), "microceph", name, "after")


def test_no_units_is_not_success(tmp_path):
    q = implementation().Qualification(manifest(), tmp_path / "evidence.jsonl")
    with pytest.raises(AssertionError):
        q.audit(Juju(observed(), {}), "microceph", "module::test", "before")


def test_qualified_sunbeam_artifact_base_is_observed_not_forced():
    source = (MODULE.parents[2] / "tests/sunbeam/conftest.py").read_text()
    assert "VERSION_ID" in source
    assert "_microceph_qualification" in source


def test_optional_bootstrap_can_target_edge():
    source = (MODULE.parents[2] / "tests/scripts/bootstrap_sunbeam.sh").read_text()
    assert "MICROCEPH_TEST_SNAP_CHANNEL" in source
    assert "--manifest" in source
    assert "--role compute,storage" in source
    assert 'if [[ "${OPENSTACK_CHANNEL}" == 2026.* ]]' in source


def test_pytest_qualification_options_registered():
    from tests import conftest

    options = []
    parser = SimpleNamespace(addoption=lambda name, **kwargs: options.append(name))
    conftest.pytest_addoption(parser)
    assert "--qualification-manifest" in options
    assert "--qualification-evidence" in options


def test_recovery_qualification_has_explicit_baseline_and_target():
    source = (MODULE.parents[2] / "tests/integration/test_upgrade_health_recovery.py").read_text()
    assert 'qualification.manifest["beta"]["requested_channel"]' in source
    assert 'qualification.manifest["edge"]["requested_channel"]' in source


def test_initial_terraform_test_can_create_first_units(tmp_path):
    q = implementation().Qualification(manifest(), tmp_path / "evidence.jsonl")
    q.audit(Juju(observed(), {}), "microceph", "module::test_initial_single_unit", "before")
    with pytest.raises(AssertionError):
        q.audit(Juju(observed(), {}), "microceph", "module::test_initial_single_unit", "after")
