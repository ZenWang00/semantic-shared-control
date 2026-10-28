"""Verify the installed wheel, rather than imports from a source checkout."""

import json
import subprocess
import sys

import ssc_core


def test_import_is_standalone_and_standard_library_only(tmp_path):
    program = '''
import importlib.abc
import importlib.metadata
import json
import sys

blocked = {"numpy", "scipy", "torch", "open3d", "rclpy", "rospy", "vg_pipeline", "grasp_server", "transformers"}
class RejectHeavyImport(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in blocked:
            raise AssertionError("unexpected dependency import: " + fullname)
sys.meta_path.insert(0, RejectHeavyImport())
import ssc_core
import ssc_core.archive
import ssc_core.replay
from ssc_core import parse_record, NUMERIC_CANDIDATES_V2, SessionRegistry, parse_vlm_proposal
parse_record("Pose", {"position_xyz": [0, 0, 0], "quaternion_xyzw": [0, 0, 0, 1]}, policy=NUMERIC_CANDIDATES_V2)
registry = SessionRegistry("package-check", policy=NUMERIC_CANDIDATES_V2)
registry.register_record("TaskContext", {"task_id": "task", "revision": 1, "instruction": "approach"}, session_id="package-check")
assert registry.get_record("TaskContext", {"task_id": "task", "revision": 1}, session_id="package-check").instruction == "approach"
proposal = parse_vlm_proposal({"schema_version": "2.0", "candidates": [
    {"object_id": "cup", "task_relevance": 1.0, "point_yx": [250, 600]}]},
    known_object_ids=("cup",), task_id="task", task_revision=1, scene_id="scene", observation_id="observation")
assert proposal.candidates[0].point_yx == (250.0, 600.0)
assert not hasattr(ssc_core, "compile_program")
files = [str(p) for p in importlib.metadata.files("ssc-core")]
assert all(p.startswith(("ssc_core/", f"ssc_core-{ssc_core.__version__}.dist-info/")) for p in files), files
assert not (blocked & sys.modules.keys())
print(json.dumps({"version": importlib.metadata.version("ssc-core"), "origin": ssc_core.__file__}))
'''
    completed = subprocess.run([sys.executable, "-I", "-c", program], cwd=tmp_path, text=True, capture_output=True, check=True)
    result = json.loads(completed.stdout)
    assert result["version"] == ssc_core.__version__ == "0.11.0"
    assert "site-packages" in result["origin"]
