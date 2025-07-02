#!/usr/bin/env python3
import ast
import subprocess
import sys
import yaml


def juju_command(command):
    try:
        result = subprocess.run(
            command.split(),
            stdout=subprocess.PIPE,
        )
    except Exception as e:
        return []

    try:
        data = yaml.safe_load(result.stdout.decode("utf-8"))
        return data
    except yaml.YAMLError as e:
        return []


def run_command(cmd):
    subprocess.run(cmd.split(), stdout=subprocess.PIPE)


def get_disk_path(unit):
    command = f"juju run {unit} list-disks --format=yaml"
    disks = juju_command(command)
    unpartitioned_disks = disks[unit]["results"]["unpartitioned-disks"]
    unpartitioned_disks = ast.literal_eval(unpartitioned_disks)
    path = unpartitioned_disks[0]["path"] if unpartitioned_disks else None
    return path


def get_disks(units):

    paths = [get_disk_path(unit) for unit in units]
    return paths


def add_osd(unit, device_id, loop_spec):
    command = f"juju run {unit} add-osd device-id={device_id} loop-spec={loop_spec}"
    juju_command(command)


def main():

    if len(sys.argv) != 3:
        print("expected two argument")
        exit(1)

    device_id = sys.argv[1]
    loop_spec = sys.argv[2]
    status = juju_command("juju status --format yaml")
    apps = status.get("applications", {})

    microceph_units = []
    for _, app_data in apps.items():
        if app_data.get("charm") == "microceph":
            for unit in app_data.get("units", {}).keys():
                microceph_units.append(unit)

    for unit in microceph_units:
        add_osd(unit, device_id, loop_spec)


if __name__ == "__main__":
    main()
