# Copyright 2021-2022 NetCracker Technology Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import glob
import os
import unittest
from unittest import mock

import yaml

from kubemarine import demo, coredns, __main__
from kubemarine.core import errors, schema
from kubemarine.procedures import install
from test.unit import utils as test_utils


class FinalizedInventoryValidation(unittest.TestCase):
    def _check_finalized_validation(self, inventory: dict):
        try:
            return demo.new_cluster(inventory)
        except errors.FailException as e:
            self.fail(f"Enrichment of finalized inventory failed: {e.message}")

    def test_simple_inventory_enriches_valid(self):
        inventory = demo.generate_inventory(**demo.FULLHA_KEEPALIVED)
        cluster = demo.new_cluster(inventory)

        test_utils.stub_associations_packages(cluster, {})
        finalized_inventory = test_utils.make_finalized_inventory(cluster)

        # check that enrichment of finalized inventory is successful and the inventory is valid against the schema
        cluster = self._check_finalized_validation(finalized_inventory)

        test_utils.stub_associations_packages(cluster, {})
        finalized_inventory = test_utils.make_finalized_inventory(cluster)

        # check that enrichment is idempotent and double-finalized inventory still valid against the schema
        self._check_finalized_validation(finalized_inventory)

    def test_coredns_generation_enriches_valid(self):
        inventory = demo.generate_inventory(**demo.MINIHA)
        cluster = demo.new_cluster(inventory)
        coredns.generate_configmap(cluster.inventory)

        test_utils.stub_associations_packages(cluster, {})
        finalized_inventory = test_utils.make_finalized_inventory(cluster)

        # check that generation of coredns does not break finalized inventory
        self._check_finalized_validation(finalized_inventory)

    @mock.patch('kubemarine.plugins.install_plugin')
    def test_plugins_installation_enriches_valid(self, install_plugin):
        inventory = demo.generate_inventory(**demo.MINIHA)
        cluster = demo.new_cluster(inventory)
        install.deploy_plugins(cluster)

        test_utils.stub_associations_packages(cluster, {})
        finalized_inventory = test_utils.make_finalized_inventory(cluster)

        # check that plugins installation does not break finalized inventory
        self._check_finalized_validation(finalized_inventory)


class TestValidExamples(unittest.TestCase):
    def test_cluster_examples_valid(self):
        inventories_dir = os.path.abspath(f"{__file__}/../../../../examples/cluster.yaml")
        self.assertTrue(os.path.isdir(inventories_dir), "Examples not found")
        for inventory_filepath in glob.glob(inventories_dir + "/**/*", recursive=True):
            if os.path.isdir(inventory_filepath) or 'cluster' not in os.path.basename(inventory_filepath):
                continue
            with open(inventory_filepath, 'r') as stream:
                inventory = yaml.safe_load(stream)

            # check that enrichment is successful and the inventory is valid against the schema
            context = demo.create_silent_context()
            context['nodes'] = demo.generate_nodes_context(inventory)
            try:
                cluster = demo.FakeKubernetesCluster(inventory, context=context)
                schema.verify_inventory(cluster.raw_inventory, cluster)
            except Exception as e:
                self.fail(f"Enrichment of {os.path.relpath(inventory_filepath, start=inventories_dir)} failed: {e}")

    def test_procedure_examples_valid(self):
        inventories_dir = os.path.abspath(f"{__file__}/../../../../examples/procedure.yaml")
        self.assertTrue(os.path.isdir(inventories_dir), "Examples not found")
        for inventory_filepath in glob.glob(inventories_dir + "/**/*", recursive=True):
            if os.path.isdir(inventory_filepath):
                continue
            with open(inventory_filepath, 'r') as stream:
                procedure_inventory = yaml.safe_load(stream)

            relpath = os.path.relpath(inventory_filepath, start=inventories_dir)
            for procedure in __main__.procedures.keys():
                if procedure in os.path.basename(inventory_filepath):
                    break
            else:
                self.fail(f"Unknown procedure for inventory {relpath}")

            context = demo.create_silent_context(procedure=procedure)
            inventory = demo.generate_inventory(**demo.MINIHA)

            # check that enrichment is successful and the inventory is valid against the schema
            try:
                demo.new_cluster(inventory, context=context, procedure_inventory=procedure_inventory)
            except Exception as e:
                self.fail(f"Enrichment of {relpath} failed: {e}")


class TestErrorHeuristics(unittest.TestCase):
    def test_not_of_types(self):
        """
        'vrrp_ips' section is an example where each item can be either string or object.
        Specify some other type to check correctly generated error.
        See kubemarine.core.schema._unnest_type_subschema_errors
        """
        inventory = demo.generate_inventory(**demo.ALLINONE)
        inventory['vrrp_ips'][0] = 123
        with self.assertRaisesRegex(errors.FailException,
                                    r"Actual instance type is '(integer|number)'\. Expected: 'string', 'object'\."):
            demo.new_cluster(inventory)

    def test_key_not_in_propertyNames(self):
        """
        'nodes' section is an example where propertyNames are configured as allOf(enums).
        Specify unexpected property to check correctly generated error.
        See kubemarine.core.schema._unnest_enum_subschema_errors
        """
        inventory = demo.generate_inventory(**demo.ALLINONE)
        inventory['nodes'][0]['unsupported_property'] = 'value'
        with self.assertRaisesRegex(errors.FailException, r"Property name 'unsupported_property' is not one of \[.*]"):
            demo.new_cluster(inventory)

    def test_raise_max_relevant_from_subschema(self):
        """
        'vrrp_ips' section is an example where each item can be either string or object.
        If object is supplied, the most relevant error should be raised.
        See kubemarine.core.schema._descend_errors
        """
        inventory = demo.generate_inventory(**demo.ALLINONE)
        inventory['vrrp_ips'][0] = {
            "ip": inventory['vrrp_ips'][0],
            "unsupported_property": 'value',  # unsupported property has greater priority
            "hosts": [
                {
                    "priority": -1  # break the lower bound has lower priority
                }
            ]
        }
        with self.assertRaisesRegex(errors.FailException, r"'unsupported_property' was unexpected"):
            demo.new_cluster(inventory)

    def test_oneOf_object_with_propertyNames(self):
        """
        'services.thirdparties' section is an example where each item can be either string or object,
        and the schema for object is configured with propertyNames assertion.
        Specify unexpected property to check correctly generated error.
        See kubemarine.core.schema._apply_property_names_heuristic
        """
        inventory = demo.generate_inventory(**demo.ALLINONE)
        inventory['services']['thirdparties'] = {
            '/usr/bin/kubeadm': {
                "source": "http://source",
                "unsupported_property": "value"
            }
        }
        with self.assertRaisesRegex(errors.FailException, r"Property name 'unsupported_property' is not one of \[.*]"):
            demo.new_cluster(inventory)

    def test_list_merging_strong_heuristic(self):
        """
        'services.audit.cluster_policy.rules' section is an example where each item can be oneOf(object, list merging symbol).
        Omit required property for object to check correctly generated error.
        See kubemarine.core.schema._apply_list_merging_strong_heuristic
        """
        inventory = demo.generate_inventory(**demo.ALLINONE)
        inventory['services']['audit'] = {
            'cluster_policy': {
                "rules": [
                    {"<<": "merge"},
                    {"level property present": False}
                ]
            }
        }
        with self.assertRaisesRegex(errors.FailException, r"'level' is a required property"):
            demo.new_cluster(inventory)

    def test_required_and_optional_properties_heuristic(self):
        """
        'registry' section is an example where of oneOf(object, object).
        Specify unexpected properties to check correctly generated error.
        See kubemarine.core.schema._apply_required_and_optional_properties_heuristic
        """
        inventory = demo.generate_inventory(**demo.ALLINONE)
        inventory['registry'] = {
            'address': 'example.com',
            'unsupported_property': False
        }
        with self.assertRaisesRegex(errors.FailException, r"'unsupported_property' was unexpected"):
            demo.new_cluster(inventory)

        inventory['registry'] = {
            'address': 'example.com',
            'endpoints': ['one', 'another']
        }
        with self.assertRaisesRegex(errors.FailException, r"'address' was unexpected"):
            demo.new_cluster(inventory)


if __name__ == '__main__':
    unittest.main()
