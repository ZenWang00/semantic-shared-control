"""Verify the installed wheel, rather than imports from a source checkout."""

import json
import subprocess
import sys

import ssc_core


def test_import_is_standalone_and_standard_library_only(tmp_path):
    program = '''
import hashlib
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
from ssc_core import parse_record, NUMERIC_OFFLINE_V1, SessionRegistry, compile_program
parse_record("Pose", {"position_xyz": [0, 0, 0], "quaternion_xyzw": [0, 0, 0, 1]}, policy=NUMERIC_OFFLINE_V1)
registry = SessionRegistry("package-check", policy=NUMERIC_OFFLINE_V1)
registry.register_record("TaskContext", {"task_id": "task", "revision": 1, "instruction": "approach"}, session_id="package-check")
assert registry.get_record("TaskContext", {"task_id": "task", "revision": 1}, session_id="package-check").instruction == "approach"
source = "def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params):\\n    return norm(p_tcp)\\n"
program = {"program_id": "program-package-check", "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
           "function_api_version": "geometry-residual-v1", "object_id": "object", "description": "package check",
           "entrypoint": "evaluate", "source_code": source, "keypoint_bindings": [], "axis_bindings": [],
           "parameters": [], "output_unit": "m"}
compiled = compile_program(program, policy=NUMERIC_OFFLINE_V1)
assert compiled.evaluate([3, 4, 0], [[1, 0, 0], [0, 1, 0], [0, 0, 1]], [], [], []) == 5.0
files = [str(p) for p in importlib.metadata.files("ssc-core")]
assert all(p.startswith(("ssc_core/", f"ssc_core-{ssc_core.__version__}.dist-info/")) for p in files), files
assert not (blocked & sys.modules.keys())
print(json.dumps({"version": importlib.metadata.version("ssc-core"), "origin": ssc_core.__file__}))
'''
    completed = subprocess.run([sys.executable, "-I", "-c", program], cwd=tmp_path, text=True, capture_output=True, check=True)
    result = json.loads(completed.stdout)
    assert result["version"] == ssc_core.__version__ == "0.6.0"
    assert "site-packages" in result["origin"]
