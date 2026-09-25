from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bbw3d._http import Bbw3dError  # noqa: E402
from bbw3d.job import Job  # noqa: E402
from bbw3d.spec import DesignSpec  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"x"


class TestJob(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.image = self.root / "Phone Stand.png"
        self.image.write_bytes(PNG)

    def tearDown(self):
        self._tmp.cleanup()

    def new_job(self) -> Job:
        return Job.new(image=self.image, out_root=self.root / "out")

    def test_new_job_copies_source_and_builds_folders(self):
        job = self.new_job()
        self.assertTrue(job.dir.name.startswith("phone-stand-"))
        self.assertEqual(job.source_image.read_bytes(), PNG)
        for sub in ("code", "renders", "exports"):
            self.assertTrue((job.dir / sub).is_dir())
        self.assertEqual(job.model_name, "phone-stand")
        self.assertEqual(job.round, 0)

    def test_rounds_and_artifacts_are_recorded(self):
        job = self.new_job()
        self.assertEqual(job.next_round(), 1)
        code_path = job.save_code("from build123d import *\n")
        render_path = job.save_render("multiview-iso", PNG)
        export_path = job.save_export("phone-stand", b"solid\n", "stl")
        self.assertEqual(code_path.name, "round-01.py")
        self.assertEqual(render_path.name, "r01-multiview-iso.png")
        self.assertEqual(export_path.name, "phone-stand.stl")
        self.assertEqual(job.next_round(), 2)
        self.assertEqual(job.save_code("x").name, "round-02.py")

    def test_event_log_appends_and_survives_reload(self):
        job = self.new_job()
        job.log("model.create", round=1, model="phone-stand")
        job.log("render", round=1, kind="multiview")
        events = Job.load(job.dir).manifest["events"]
        self.assertEqual([e["event"] for e in events],
                         ["job.created", "model.create", "render"])
        self.assertTrue(all("at" in e for e in events))

    def test_latest_picks_the_newest_job(self):
        first = self.new_job()
        second = Job.new(image=self.image, name="second", out_root=self.root / "out")
        second.dir.touch()
        latest = Job.latest(out_root=self.root / "out")
        self.assertIn(latest.dir, (first.dir, second.dir))
        self.assertEqual(latest.dir, second.dir)

    def test_load_rejects_a_folder_that_is_not_a_job(self):
        with self.assertRaises(Bbw3dError):
            Job.load(self.root)

    def test_latest_with_no_jobs_explains_what_to_do(self):
        with self.assertRaises(Bbw3dError) as ctx:
            Job.latest(out_root=self.root / "empty")
        self.assertIn("bbw3d new", str(ctx.exception))

    def test_missing_image_fails_loudly(self):
        with self.assertRaises(Bbw3dError):
            Job.new(image=self.root / "nope.png", out_root=self.root / "out")


class TestDesignSpec(unittest.TestCase):
    def good(self) -> DesignSpec:
        return DesignSpec(
            name="phone-stand",
            summary="Wedge-shaped stand seen from the side in the sketch.",
            features=["wedge 70x80x60", "lip 8mm tall at the front"],
            overall={"x": 70.0, "y": 80.0, "z": 60.0},
        )

    def test_complete_spec_has_no_problems(self):
        self.assertEqual(self.good().problems(), [])

    def test_missing_third_dimension_is_flagged(self):
        spec = self.good()
        spec.overall.pop("z")
        problems = " ".join(spec.problems())
        self.assertIn("['z']", problems)

    def test_empty_spec_flags_every_gap(self):
        problems = DesignSpec(name="").problems()
        joined = " ".join(problems)
        for expected in ("no name", "no summary", "no features", "missing"):
            self.assertIn(expected, joined)

    def test_nonsense_dimension_is_flagged(self):
        spec = self.good()
        spec.overall["x"] = -5
        self.assertTrue(any("not a positive number" in p for p in spec.problems()))

    def test_round_trip_through_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spec.json"
            spec = self.good()
            spec.assumptions = ["depth not shown; assumed 80mm"]
            spec.write(path)
            self.assertEqual(DesignSpec.read(path).to_dict(), spec.to_dict())

    def test_unknown_keys_are_ignored_on_read(self):
        spec = DesignSpec.from_dict({"name": "x", "bogus": 1})
        self.assertEqual(spec.name, "x")


if __name__ == "__main__":
    unittest.main()
