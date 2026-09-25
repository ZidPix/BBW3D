"""Client tests. Stdlib unittest, no container, no network: python3 -m unittest -v"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bbw3d._http import Bbw3dError, Response  # noqa: E402
from bbw3d.cad_client import CadClient, extract_assets, slug  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-pixels"


def json_response(payload: dict, status: int = 200) -> Response:
    return Response(status=status, content_type="application/json",
                    body=json.dumps(payload).encode(), url="http://test/x")


def recorder(response_for=None):
    """Fake transport that records calls and replays canned responses."""
    calls: list[dict] = []

    def transport(url, method="GET", payload=None, timeout=None):
        calls.append({"url": url, "method": method, "payload": payload})
        if callable(response_for):
            return response_for(url, payload)
        return response_for if response_for is not None else json_response({"status": "ok"})

    transport.calls = calls
    return transport


class TestExtractAssets(unittest.TestCase):
    def test_raw_png_body(self):
        resp = Response(200, "image/png", PNG)
        self.assertEqual(extract_assets(resp), [("render", PNG)])

    def test_png_body_without_content_type(self):
        """Magic bytes win over a missing or wrong content-type header."""
        self.assertEqual(extract_assets(Response(200, "", PNG))[0][1], PNG)

    def test_base64_anywhere_in_json(self):
        payload = {"model": "box", "renders": {"front": base64.b64encode(PNG).decode()}}
        assets = extract_assets(json_response(payload))
        self.assertEqual(assets, [("renders.front", PNG)])

    def test_data_uri(self):
        uri = "data:image/png;base64," + base64.b64encode(PNG).decode()
        assets = extract_assets(json_response({"image": uri}))
        self.assertEqual(assets, [("image", PNG)])

    def test_list_of_images_keeps_order(self):
        b64 = base64.b64encode(PNG).decode()
        payload = {"images": [b64, base64.b64encode(PNG + b"2").decode()]}
        labels = [label for label, _ in extract_assets(json_response(payload))]
        self.assertEqual(labels, ["images.0", "images.1"])

    def test_workspace_path_is_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "renders").mkdir()
            (ws / "renders" / "iso.png").write_bytes(PNG)
            payload = {"path": "/workspace/renders/iso.png"}
            assets = extract_assets(json_response(payload), search=[ws])
            self.assertEqual(assets, [("path", PNG)])

    def test_unresolvable_path_is_ignored(self):
        payload = {"path": "/workspace/renders/missing.png"}
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(extract_assets(json_response(payload), search=[Path(tmp)]), [])

    def test_plain_strings_are_not_mistaken_for_assets(self):
        payload = {"status": "ok", "message": "model created", "name": "box"}
        self.assertEqual(extract_assets(json_response(payload)), [])


class TestCadClientContract(unittest.TestCase):
    def test_create_posts_documented_payload(self):
        t = recorder()
        CadClient(base_url="http://cad:8123", transport=t).create("widget", "code()")
        self.assertEqual(t.calls[0]["url"], "http://cad:8123/model/create")
        self.assertEqual(t.calls[0]["method"], "POST")
        self.assertEqual(t.calls[0]["payload"], {"name": "widget", "code": "code()"})

    def test_render_uses_model_name_key(self):
        t = recorder(json_response({"image": base64.b64encode(PNG).decode()}))
        result = CadClient(transport=t).render("widget", kind="3d", view="front")
        self.assertEqual(t.calls[0]["payload"], {"model_name": "widget", "view": "front"})
        self.assertTrue(t.calls[0]["url"].endswith("/render/3d"))
        self.assertEqual(result.kind, "3d")
        self.assertEqual(len(result.images), 1)

    def test_measure_is_a_get_with_name_in_path(self):
        t = recorder(json_response({"x": 30}))
        CadClient(base_url="http://cad:8123", transport=t).measure("widget")
        self.assertEqual(t.calls[0]["method"], "GET")
        self.assertEqual(t.calls[0]["url"], "http://cad:8123/model/widget/measure")

    def test_export_sends_format_and_returns_bytes(self):
        stl = b"solid probe\nendsolid probe\n"
        resp = Response(200, "application/octet-stream", stl)
        meta, assets = CadClient(transport=recorder(resp)).export("widget", fmt="stl")
        self.assertEqual(meta, {})
        self.assertEqual(assets, [("asset", stl)])

    def test_render_without_image_is_an_error_not_a_silent_pass(self):
        t = recorder(json_response({"status": "ok"}))
        with self.assertRaises(Bbw3dError) as ctx:
            CadClient(transport=t).render("widget")
        self.assertIn("no image", str(ctx.exception))

    def test_bad_kind_and_format_rejected_before_any_request(self):
        t = recorder()
        client = CadClient(transport=t)
        with self.assertRaises(Bbw3dError):
            client.render("widget", kind="hologram")
        with self.assertRaises(Bbw3dError):
            client.export("widget", fmt="obj")
        self.assertEqual(t.calls, [])

    def test_http_error_surfaces_container_message(self):
        resp = Response(500, "application/json",
                        b'{"detail":"NameError: name Bx is not defined"}')
        with self.assertRaises(Bbw3dError) as ctx:
            CadClient(transport=recorder(resp)).create("widget", "Bx()")
        self.assertIn("NameError", str(ctx.exception))

    def test_base_url_trailing_slash_does_not_double_up(self):
        t = recorder()
        CadClient(base_url="http://cad:8123/", transport=t).health()
        self.assertEqual(t.calls[0]["url"], "http://cad:8123/health")


class TestSlug(unittest.TestCase):
    def test_slugs(self):
        self.assertEqual(slug("Phone Stand v2!"), "phone-stand-v2")
        self.assertEqual(slug("  "), "model")


if __name__ == "__main__":
    unittest.main()
