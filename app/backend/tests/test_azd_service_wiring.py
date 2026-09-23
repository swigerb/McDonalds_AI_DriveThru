"""Guard the azd service name <-> infra parameter wiring (ported from Sonic ecc9c55).

azd exports SERVICE_<NAME>_RESOURCE_EXISTS for each service in azure.yaml
(name upper-cased, '-' -> '_'). If main.parameters.json reads a variable for a
service that doesn't exist, `exists` is always false and every `azd provision`
redeploys the container app with the helloworld placeholder image. McDonald's
had the same bug as Sonic: SERVICE_WEB_* was read while the service is `backend`.

Also pins the shared-Azure-OpenAI guard: the McD demo reuses Sonic's OpenAI
account (rg-sonic-demo), so the account/deployments module must stay
conditional on reuseExistingOpenAi or a provision would redeclare Sonic's
deployments at McD's capacity.
"""

import json
import re
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
AZURE_YAML = REPO / "azure.yaml"
PARAMS = REPO / "infra" / "main.parameters.json"
MAIN_BICEP = REPO / "infra" / "main.bicep"

_EXISTS_VAR = re.compile(r"\$\{SERVICE_([A-Z0-9_]+)_RESOURCE_EXISTS(?:=[^}]*)?\}")
_SERVICE_TAG = re.compile(r"'azd-service-name'\s*:\s*'([^']+)'")
_OPENAI_RG_SCOPED = re.compile(
    r"^(?:module|resource)\s+(\w+)\s+'[^']+'\s*=\s*([^{\n]*)\{(?:\s*\n\s*name:[^\n]*)?\s*\n\s*scope:\s*openAiResourceGroup\b",
    re.MULTILINE)


def _azd_env_name(service: str) -> str:
    return service.upper().replace("-", "_")


class AzdServiceWiringTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.services = yaml.safe_load(AZURE_YAML.read_text(encoding="utf-8"))["services"]
        cls.params = json.loads(PARAMS.read_text(encoding="utf-8"))["parameters"]

    def _exists_vars(self) -> dict[str, str]:
        found = {}
        for param, spec in self.params.items():
            match = _EXISTS_VAR.search(json.dumps(spec.get("value", "")))
            if match:
                found[param] = match.group(1)
        return found

    def test_resource_exists_vars_name_real_azd_services(self):
        exists_vars = self._exists_vars()
        self.assertTrue(exists_vars, "no SERVICE_*_RESOURCE_EXISTS mapping found in main.parameters.json")
        known = {_azd_env_name(s): s for s in self.services}
        for param, env_service in exists_vars.items():
            self.assertIn(
                env_service, known,
                f"{param} reads SERVICE_{env_service}_RESOURCE_EXISTS, but azure.yaml services are "
                f"{sorted(self.services)} -> azd never sets it, so `exists` is always false and "
                "provision falls back to the helloworld image")

    def test_every_containerapp_service_has_an_exists_mapping(self):
        mapped = set(self._exists_vars().values())
        for name, svc in self.services.items():
            if svc.get("host") == "containerapp":
                self.assertIn(_azd_env_name(name), mapped,
                              f"containerapp service '{name}' has no SERVICE_*_RESOURCE_EXISTS parameter")

    def test_web_app_exists_feeds_the_backend_container_app(self):
        self.assertEqual(self.params["webAppExists"]["value"], "${SERVICE_BACKEND_RESOURCE_EXISTS=false}")
        self.assertRegex(MAIN_BICEP.read_text(encoding="utf-8"), r"\bexists:\s*webAppExists\b")

    def test_bicep_service_tags_match_azure_yaml(self):
        tags = set(_SERVICE_TAG.findall(MAIN_BICEP.read_text(encoding="utf-8")))
        self.assertTrue(tags, "no azd-service-name tag found in main.bicep")
        self.assertLessEqual(tags, set(self.services), "azd-service-name tag not declared in azure.yaml")


class SharedOpenAiGuardTests(unittest.TestCase):

    def setUp(self):
        self.bicep = MAIN_BICEP.read_text(encoding="utf-8")

    def test_openai_account_and_deployments_only_when_not_reusing(self):
        self.assertRegex(self.bicep, r"module openAi '[^']+' = if \(!reuseExistingOpenAi\) \{")
        self.assertNotIn("Microsoft.CognitiveServices/accounts", self.bicep,
                         "declare OpenAI resources only through the conditional openAi module")
        self.assertEqual(self.params_value("reuseExistingOpenAi"), "${AZURE_OPENAI_REUSE_EXISTING=false}")

    def test_only_role_assignments_touch_the_shared_openai_resource_group(self):
        scoped = dict(_OPENAI_RG_SCOPED.findall(self.bicep))
        self.assertEqual(len(scoped), len(re.findall(r"scope:\s*openAiResourceGroup\b", self.bicep)),
                         "a declaration scoped to openAiResourceGroup was not recognised; review it by hand")
        self.assertIn("openAi", scoped)
        for name, condition in scoped.items():
            if name == "openAi":
                self.assertIn("!reuseExistingOpenAi", condition)
            else:
                self.assertRegex(name, r"^openAiRole", f"{name} is scoped to the (possibly shared) OpenAI RG")

    @staticmethod
    def params_value(name: str) -> str:
        return json.loads(PARAMS.read_text(encoding="utf-8"))["parameters"][name]["value"]


if __name__ == "__main__":
    unittest.main()
