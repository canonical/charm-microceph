# Copyright 2023 Canonical Ltd.
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

"""Compatibility wrappers for integration relation-data helpers."""

from tests import helpers


def get_unit_info(unit: str, model: str) -> dict:
    """Return unit info from ``juju show-unit`` output."""
    return helpers.get_unit_info(unit, model)


def get_relation_data(unit: str, endpoint: str, related_unit: str, model: str) -> dict:
    """Return relation data for a specific endpoint and related unit."""
    return helpers.get_relation_data(unit, endpoint, related_unit, model)
