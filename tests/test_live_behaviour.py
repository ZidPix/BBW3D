"""Tests written against what the real container actually did.

Every case here comes from out/verify-report.json produced by a live
cad-agent on 2026-09-25, not from its documentation.
"""

from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bbw3d._http import Bbw3dError, Response  # noqa: E402
from bbw3d.cad_client import (CadClient, raise_if_failed,  # noqa: E402
                              says_missing_model)
from bbw3d.verify import PROBE_DIALECTS, run_verification  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"pixels"


def json_response(payload: dict, status: int = 200) -> Response:
    return Response(status=status, content_type="application/json",
                    body=json.dumps(payload).encode(), url="http://test/x")


class FakeContainer:
    """Mimics the observed container: refuses imports, honours only `name`,
    answers 200 with success=false, and has no /render/blueprint."""

    def __init__(self, accepted_dialect: str = "builder-part", model_key: str = "name"):
        self.accepted_dialect = accepted_dialect
        self.model_key = model_key
        self.models: dict[str, str] = {}
        self.calls: list[dict] = []

    def __call__(self, url, method="GET", payload=None, timeout=None):
        self.calls.append({"url": url, "method": method, "payload": payload})
        path = url.split("8123", 1)[-1] if "8123" in url else url
        payload = payload or {}
        name = payload.get(self.model_key)

        if path.endswith("/health"):
            return json_response({"status": "ok", "version": "0.1.0"})
        if path.endswith("/model/list"):
            return json_response({"models": sorted(self.models)})
        if path.endswith("/measure"):
            return json_response({"bounding_box": {"x": 30.0, "y": 20.0, "z": 10.0}}
                                 if self.models else {"error": "No model available"})
        if path.endswith("/render/blueprint"):
            return json_response({"detail": "Not Found"}, status=404)

        if path.endswith(("/model/create", "/model/modify")):
            code = payload.get("code", "")
            if "import " in code:
                return json_response({"success": False, "output": "",
                                      "error": "Security Error: Forbidden keyword "
                                               "'import ' detected.", "geometry": None})
            expected = dict(PROBE_DIALECTS)[self.accepted_dialect]
            normalise = lambda s: s.replace("30, 20, 14", "").replace("30, 20, 10", "")
            if normalise(code) != normalise(expected):
                return json_response({"success": False, "output": "",
                                      "error": "NameError: name 'Box' is not defined",
                                      "geometry": None})
            if not name:
                return json_response({"error": "No model 'None' available"}, status=400)
            if "result" not in code:
                # The real container's silent failure: success, but nothing stored,
                # and the explanatory warning is overwritten by its own finally block.
                return json_response({"success": True, "output": "", "error": "",
                                      "geometry": None})
            self.models[name] = code
            return json_response({"success": True, "name": name,
                                  "geometry": {"volume": 6000.0}})

        # Model-scoped reads: an unknown key means the server substitutes a default.
        if not name:
            if path.endswith("/analyze/printability"):
                return json_response({"error": "No model 'active' found"})
            return json_response({"detail": "No model 'None' available"}, status=400)
        if name not in self.models:
            return json_response({"error": f"No model '{name}' found"})

        if path.endswith("/analyze/printability"):
            return json_response({"manifold": True, "watertight": True})
        if "/render/" in path:
            return json_response({"views": {"iso": base64.b64encode(PNG).decode()}})
        if path.endswith("/export"):
            return Response(200, "application/octet-stream", b"solid x\nendsolid x\n",
                            url=url)
        return json_response({"detail": "Not Found"}, status=404)


class TestObservedFailureModes(unittest.TestCase):
    def test_success_false_is_an_error_not_a_pass(self):
        """HTTP 200 + success:false is how a refusal arrives. The original
        client returned it as success, so every later call failed instead."""
        resp = json_response({"success": False, "error": "Security Error: Forbidden "
                                                         "keyword 'import ' detected."})
        with self.assertRaises(Bbw3dError) as ctx:
            raise_if_failed(resp.json(), "create 'box'")
        self.assertIn("Forbidden keyword", str(ctx.exception))

    def test_import_is_refused_before_the_request_is_sent(self):
        client = CadClient(transport=FakeContainer())
        with self.assertRaises(Bbw3dError) as ctx:
            client.create("box", "from build123d import *\nBox(1,1,1)\n")
        self.assertIn("drop the import line", str(ctx.exception))

    def test_missing_model_phrases_are_recognised(self):
        for error in ("No model available", "No model 'None' available",
                      "No model 'active' found"):
            with self.subTest(error=error):
                self.assertTrue(says_missing_model(json_response({"error": error})))
        self.assertFalse(says_missing_model(json_response({"manifold": True})))

    def test_blueprint_404_gives_an_actionable_message(self):
        fake = FakeContainer()
        client = CadClient(transport=fake)
        client.create("box", dict(PROBE_DIALECTS)["builder-part"])
        with self.assertRaises(Bbw3dError) as ctx:
            client.render("box", kind="blueprint")
        self.assertIn("no /render/blueprint endpoint", str(ctx.exception))


class TestModelKeyDiscovery(unittest.TestCase):
    def test_finds_the_key_the_container_honours(self):
        fake = FakeContainer(model_key="name")
        client = CadClient(transport=fake)
        client.create("box", dict(PROBE_DIALECTS)["builder-part"])
        self.assertEqual(client.model_key, "name")

    def test_falls_back_when_the_other_key_is_the_live_one(self):
        fake = FakeContainer(model_key="model_name")
        client = CadClient(transport=fake)
        client.create("box", dict(PROBE_DIALECTS)["builder-part"])
        self.assertEqual(client.model_key, "model_name")

    def test_discovered_key_is_reused_not_rediscovered(self):
        fake = FakeContainer(model_key="model_name")
        client = CadClient(transport=fake)
        client.create("box", dict(PROBE_DIALECTS)["builder-part"])
        before = len(fake.calls)
        client.printability("box")
        self.assertEqual(len(fake.calls) - before, 1, "should not retry both keys again")


class TestVerificationRun(unittest.TestCase):
    def test_discovers_dialect_key_and_reports_blueprint_unavailable(self):
        client = CadClient(transport=FakeContainer(accepted_dialect="algebra-result"))
        report = run_verification(client)

        self.assertEqual(report["working_dialect"], "algebra-result")
        self.assertEqual(report["model_key_used"], "name")
        self.assertTrue(report["steps"]["render/blueprint"].get("unavailable"))
        self.assertEqual(
            report["steps"]["model/measure"]["expected_30_20_10_found"],
            [10.0, 20.0, 30.0])
        self.assertTrue(report["ok"], report.get("failed"))

    def test_records_every_rejected_dialect_with_its_reason(self):
        client = CadClient(transport=FakeContainer(accepted_dialect="builder-object"))
        report = run_verification(client)
        rejected = [d for d in report["code_dialects"] if not d["accepted"]]
        self.assertTrue(rejected)
        self.assertIn("NameError", rejected[0]["error"])

    def test_reports_cleanly_when_nothing_is_accepted(self):
        class RefuseAll(FakeContainer):
            def __call__(self, url, method="GET", payload=None, timeout=None):
                if "/model/create" in url:
                    return json_response({"success": False, "error": "Security Error: "
                                                                     "Forbidden keyword"})
                return super().__call__(url, method, payload, timeout)

        report = run_verification(CadClient(transport=RefuseAll()))
        self.assertFalse(report["ok"])
        self.assertIsNone(report["working_dialect"])
        self.assertEqual(len(report["code_dialects"]), len(PROBE_DIALECTS))

    def test_no_probe_dialect_contains_an_import(self):
        for name, code in PROBE_DIALECTS:
            with self.subTest(dialect=name):
                self.assertNotIn("import ", code)


class TestSilentNoGeometryFailure(unittest.TestCase):
    """success=true with geometry=null means the model was NOT stored."""

    def test_create_without_result_assignment_raises(self):
        """Code the container runs happily, that nonetheless stores nothing."""
        client = CadClient(transport=FakeContainer(accepted_dialect="builder-no-result"))
        with self.assertRaises(Bbw3dError) as ctx:
            client.create("box", dict(PROBE_DIALECTS)["builder-no-result"])
        message = str(ctx.exception)
        self.assertIn("stored NO model", message)
        self.assertIn("result = part.part", message)

    def test_create_with_result_assignment_succeeds(self):
        client = CadClient(transport=FakeContainer())
        data = client.create("box", dict(PROBE_DIALECTS)["builder-part"])
        self.assertTrue(data["success"])
        self.assertIsNotNone(data["geometry"])

    def test_every_probe_dialect_but_the_control_assigns_result(self):
        assigning = [n for n, code in PROBE_DIALECTS if "result =" in code]
        self.assertGreaterEqual(len(assigning), 3)
        self.assertEqual(PROBE_DIALECTS[0][0], "builder-part",
                         "the most likely dialect should be tried first")


if __name__ == "__main__":
    unittest.main()
