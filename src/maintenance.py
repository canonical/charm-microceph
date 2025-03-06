# Copyright 2025 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""The maintenance module handles cluster maintenance operations."""

import logging
from socket import gethostname

import ops.charm

import charm
import microceph_client
from microceph_client import MaintenanceOperationFailedException

logger = logging.getLogger(__name__)


class Maintenance(ops.framework.Object):
    """Maintenance handles cluster maintenance operations."""

    def __init__(self, charm: "charm.MicroCephCharm"):
        super().__init__(charm, "maintenance")
        self.charm = charm
        self.framework.observe(
            self.charm.on.exit_maintenance_action, self._exit_maintenance_action
        )
        self.framework.observe(
            self.charm.on.enter_maintenance_action, self._enter_maintenance_action
        )

    def _parse_actions_from_output(self, output: dict) -> dict:
        """Get the action results from API output."""
        actions = {}
        metadata = output.get("metadata", []) or []
        for i, result in enumerate(metadata, 1):
            actions[f"step-{i}"] = {
                "description": result["action"],
                "error": result["error"],
                "id": result["name"],
            }
        logger.debug("%s", actions)
        return actions

    def _exit_maintenance_action(self, event: ops.framework.EventBase) -> None:
        """Bring the given unit out of maintenance mode."""
        dry_run = event.params.get("dry-run")
        check_only = event.params.get("check-only")
        ignore_check = event.params.get("ignore-check")
        if check_only and ignore_check:
            errors = "check-only and ignore-check cannot be used together"
            event.set_results({"actions": {}, "errors": errors, "status": "failure"})
            logger.error(errors)
            event.fail()
            return

        try:
            client = microceph_client.Client.from_socket()
            output = client.cluster.exit_maintenance_mode(
                gethostname(), dry_run, check_only, ignore_check
            )
            actions = self._parse_actions_from_output(output)
            event.set_results({"actions": actions, "errors": "", "status": "success"})
        except MaintenanceOperationFailedException as e:
            errors = str(e)
            output = e.response
            actions = self._parse_actions_from_output(output)
            logger.error("%s", errors)
            event.set_results({"actions": actions, "errors": errors, "status": "failure"})
            event.fail()
        except Exception as e:
            logger.error(
                "Failed to exit maintenance mode for unit '%s': %s", self.charm.unit.name, str(e)
            )
            event.set_results({"actions": {}, "errors": str(e), "status": "failure"})
            event.fail()

    def _enter_maintenance_action(self, event: ops.framework.EventBase) -> None:
        """Bring the given unit into maintenance mode."""
        force = event.params.get("force")
        dry_run = event.params.get("dry-run")
        set_noout = event.params.get("set-noout")
        stop_osds = event.params.get("stop-osds")
        check_only = event.params.get("check-only")
        ignore_check = event.params.get("ignore-check")
        if check_only and ignore_check:
            errors = "check-only and ignore-check cannot be used together"
            event.set_results({"actions": {}, "errors": errors, "status": "failure"})
            logger.error(errors)
            event.fail()
            return

        try:
            client = microceph_client.Client.from_socket()
            output = client.cluster.enter_maintenance_mode(
                gethostname(), force, dry_run, set_noout, stop_osds, check_only, ignore_check
            )
            actions = self._parse_actions_from_output(output)
            event.set_results({"actions": actions, "errors": "", "status": "success"})
            if force:
                logger.warning(
                    "Forced to enter maintenance mode for %s, all actions were run but "
                    "errors were ignored.",
                    self.charm.unit.name,
                )
        except MaintenanceOperationFailedException as e:
            errors = str(e)
            output = e.response
            actions = self._parse_actions_from_output(output)
            logger.error("%s", errors)
            event.set_results({"actions": actions, "errors": errors, "status": "failure"})
            event.fail()
        except Exception as e:
            logger.error(
                "Failed to enter maintenance mode for unit '%s': %s", self.charm.unit.name, str(e)
            )
            event.set_results({"actions": {}, "errors": str(e), "status": "failure"})
            event.fail()
