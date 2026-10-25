"""geometry-residual-v1 language, primitive, failure and immutability checks.

These are language and numerical checks of the installed package. They do not
establish process isolation, memory/time budgets, real-time behavior or
physical safety.
"""

import builtins
from copy import deepcopy
from dataclasses import FrozenInstanceError
from hashlib import sha256
import json
import math
from pathlib import Path
import pickle
import re

import pytest

from ssc_core import (
    NUMERIC_OFFLINE_V1, CompiledProgram, ConstraintProgram, InterpreterError, Parameter,
    ValidationPolicy, compile_program, parse_record,
)

HEADER = "def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params):\n"
WIKI = Path(__file__).resolve().parents[2] / "docs" / "wiki" / "Generated-Constraint-Program-Contract.md"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "shared_control"
IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
ATOL = RTOL = 1e-9  # numeric-offline-v1 replay residual comparison
P = (0.4, 0.0, 0.3)
KEYPOINTS = ((0.41, 0.0, 0.3), (0.5, 0.1, 0.2))
AXES = ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
PARAMS = (0.02, 0.5, -1.5)


def artifact(source, *, keypoints=2, axes=2, params=3, unit="dimensionless"):
    return {
        "program_id": "program-test", "source_sha256": sha256(source.encode("utf-8")).hexdigest(),
        "function_api_version": "geometry-residual-v1", "object_id": "object-test",
        "description": "Interpreter test relation.", "entrypoint": "evaluate", "source_code": source,
        "keypoint_bindings": [f"kp-{i}" for i in range(keypoints)],
        "axis_bindings": [f"axis-{i}" for i in range(axes)],
        "parameters": [{"name": f"param-{i}", "value": 1.0, "unit": "dimensionless"} for i in range(params)],
        "output_unit": unit,
    }


def body(*lines):
    return HEADER + "".join(f"    {line}\n" for line in lines)


def compiled(source, policy=NUMERIC_OFFLINE_V1, **sizes):
    return compile_program(artifact(source, **sizes), policy=policy)


def run(source, p=P, R=IDENTITY, keypoints=KEYPOINTS, axes=AXES, params=PARAMS, **sizes):
    return compiled(source, **sizes).evaluate(p, R, keypoints, axes, params)


def rejection(source, policy=NUMERIC_OFFLINE_V1, **sizes):
    with pytest.raises(InterpreterError) as caught:
        compiled(source, policy, **sizes)
    assert caught.value.stage == "compile"
    return caught.value


def failure(source, stage="evaluation", p=P, R=IDENTITY, keypoints=KEYPOINTS, axes=AXES, params=PARAMS, **sizes):
    program = compiled(source, **sizes)
    with pytest.raises(InterpreterError) as caught:
        program.evaluate(p, R, keypoints, axes, params)
    assert caught.value.stage == stage
    return caught.value


def rotation_z(angle):
    c, s = math.cos(angle), math.sin(angle)
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def wiki_programs():
    blocks = re.findall(r"```json\n(.*?)```", WIKI.read_text(encoding="utf-8"), re.S)
    return {item["program_id"]: item for item in map(json.loads, blocks) if "program_id" in item}


# Worked examples ----------------------------------------------------------------

def test_wiki_distance_example_matches_documented_residuals():
    program = compile_program(wiki_programs()["program-bottle-distance-v1"], policy=NUMERIC_OFFLINE_V1)
    target = [0.5, 0.1, 0.2]
    far = program.evaluate([0.6, 0.1, 0.2], IDENTITY, [target], [], [0.02])
    near = program.evaluate([0.51, 0.1, 0.2], IDENTITY, [target], [], [0.02])
    assert math.isclose(far, 0.08, rel_tol=RTOL, abs_tol=ATOL) and far > 0
    assert math.isclose(near, -0.01, rel_tol=RTOL, abs_tol=ATOL) and near < 0
    # Positive and nonpositive finite residuals are both successful evaluations.
    assert type(far) is float and type(near) is float


@pytest.mark.parametrize("axis,expected,documented", [
    ((1.0, 0.0, 0.0), math.cos(0.1745329252) ** 2 - 1, -0.03015),
    ((-1.0, 0.0, 0.0), math.cos(0.1745329252) ** 2 - 1, -0.03015),
    ((0.0, 1.0, 0.0), math.cos(0.1745329252) ** 2, 0.96985),
    ((0.0, 0.0, -1.0), math.cos(0.1745329252) ** 2, 0.96985),
])
def test_wiki_undirected_axis_example_matches_documented_residuals(axis, expected, documented):
    program = compile_program(wiki_programs()["program-bottle-axis-v1"], policy=NUMERIC_OFFLINE_V1)
    residual = program.evaluate([0.4, 0.0, 0.3], IDENTITY, [], [axis], [0.1745329252])
    assert math.isclose(residual, expected, rel_tol=RTOL, abs_tol=ATOL)
    assert round(residual, 5) == documented


@pytest.mark.parametrize("angle", [0.0, 0.3, 1.0, 2.5])
def test_wiki_undirected_axis_residual_is_sign_invariant(angle):
    program = compile_program(wiki_programs()["program-bottle-axis-v1"], policy=NUMERIC_OFFLINE_V1)
    axis = (math.cos(angle) * 0.6, math.sin(angle) * 0.6, 0.8)
    reversed_axis = tuple(-x for x in axis)
    R = rotation_z(0.7)
    forward = program.evaluate([0, 0, 0], R, [], [axis], [0.2])
    backward = program.evaluate([0, 0, 0], R, [], [reversed_axis], [0.2])
    assert math.isclose(forward, backward, rel_tol=RTOL, abs_tol=ATOL)


def test_catalog_constraint_evaluations_replay_within_profile_tolerance():
    catalog = json.loads((FIXTURES / "bottle_cup_illustrative.json").read_text())
    geometry = next(m["payload"] for m in catalog["messages"] if m["message_type"] == "SceneGeometry")
    positions = {kp["keypoint_id"]: kp["position_xyz"] for kp in geometry["keypoints"]}
    result = next(m["payload"] for m in catalog["messages"]
                  if m["message_type"] == "ControlResult" and m["payload"]["status"] == "move")
    pose = result["next_tcp_pose"]
    assert pose["quaternion_xyzw"] == [0, 0, 0, 1]
    programs = {item["program_id"]: item for item in catalog["constraint_programs"]}
    assert len(result["constraint_evaluations"]) == 2
    for recorded in result["constraint_evaluations"]:
        item = programs[recorded["program_id"]]
        program = compile_program(item, policy=NUMERIC_OFFLINE_V1)
        g = program.evaluate(pose["position_xyz"], IDENTITY,
                             [positions[key] for key in item["keypoint_bindings"]], [],
                             [parameter["value"] for parameter in item["parameters"]])
        assert math.isclose(g, recorded["raw_g"], rel_tol=RTOL, abs_tol=ATOL)


def test_offline_fixture_distance_programs_evaluate_at_initial_pose():
    catalog = json.loads((FIXTURES / "bottle_cup_offline_v1.json").read_text())
    geometry = next(m["payload"] for m in catalog["messages"] if m["message_type"] == "SceneGeometry")
    positions = {kp["keypoint_id"]: kp["position_xyz"] for kp in geometry["keypoints"]}
    for item in catalog["constraint_programs"]:
        program = compile_program(item, policy=NUMERIC_OFFLINE_V1)
        g = program.evaluate(catalog["initial_pose"]["position_xyz"], IDENTITY,
                             [positions[key] for key in item["keypoint_bindings"]], [], [0])
        assert math.isclose(g, 0.01, rel_tol=RTOL, abs_tol=ATOL)


def test_profile_directed_axis_program_and_axis_alignment_oracle():
    source = body("return acos(clip(dot(R_control_tcp[:, 0], axis_dirs[0]), -1, 1)) - params[0]")
    program = compiled(source, keypoints=0, axes=1, params=1)
    A = 0.1
    target = (math.cos(A), math.sin(A), 0.0)
    assert math.isclose(program.evaluate(P, IDENTITY, [], [target], [0.0]), A, rel_tol=RTOL, abs_tol=ATOL)
    # offline-reference-v1 axis-alignment oracle: rotation 5A/6 leaves residual A/6.
    assert math.isclose(program.evaluate(P, rotation_z(5 * A / 6), [], [target], [0.0]), A / 6,
                        rel_tol=RTOL, abs_tol=ATOL)
    # A directed formula distinguishes the opposed direction.
    opposed = tuple(-x for x in target)
    assert math.isclose(program.evaluate(P, IDENTITY, [], [opposed], [0.0]), math.pi - A,
                        rel_tol=RTOL, abs_tol=ATOL)


def test_profile_division_variant_is_finite_at_probes_and_fails_at_runtime_pose():
    source = body("return params[0] / (p_tcp[0] - params[1])")
    program = compiled(source, keypoints=0, axes=0, params=2)
    assert math.isclose(program.evaluate((0.4, 0, 0.3), IDENTITY, [], [], [0.01, 0.405]), -2.0, rel_tol=1e-9)
    with pytest.raises(InterpreterError) as caught:
        program.evaluate((0.405, 0, 0.3), IDENTITY, [], [], [0.01, 0.405])
    assert (caught.value.stage, caught.value.code) == ("evaluation", "division_by_zero")


# Accepted language ----------------------------------------------------------------

M = rotation_z(0.3)
ACCEPTED = [
    ("literal integer", ["return 2"], 2.0),
    ("literal float and underscore", ["return 1_000.5"], 1000.5),
    ("hex literal", ["return 0x10"], 16.0),
    ("unary plus and minus", ["return -(+params[2])"], 1.5),
    ("all four operators", ["return (params[0] + params[1]) * params[2] / 2 - 1"], (0.02 + 0.5) * -1.5 / 2 - 1),
    ("scalar times vector", ["v = 2 * p_tcp", "return v[0] + v[2]"], 0.8 + 0.6),
    ("vector divided by scalar", ["v = p_tcp / 2", "return v[0]"], 0.2),
    ("scalar minus vector", ["v = 1 - p_tcp", "return v[2]"], 0.7),
    ("elementwise vectors", ["v = p_tcp * keypoints[0]", "return v[0]"], 0.4 * 0.41),
    ("elementwise matrices", ["m = R_control_tcp * R_control_tcp", "return m[0][1]"], M[0][1] ** 2),
    ("negated matrix", ["m = -keypoints", "return m[1, 2]"], -0.2),
    ("matrix column slice", ["return R_control_tcp[:, 0][1]"], M[1][0]),
    ("matrix row", ["return R_control_tcp[0][1]"], M[0][1]),
    ("matrix element tuple index", ["return R_control_tcp[1, 0]"], M[1][0]),
    ("row with full slice", ["return R_control_tcp[0, :][1]"], M[0][1]),
    ("full slices", ["v = p_tcp[:]", "m = keypoints[:, :]", "return v[0] + m[1][0]"], 0.9),
    ("column of feature matrix", ["c = keypoints[:, 2]", "return c[0] - c[1]"], 0.3 - 0.2),
    ("index of call result", ["return cross(axis_dirs[0], axis_dirs[1])[1]"], 1.0),
    ("norm", ["return norm(p_tcp - keypoints[0])"], math.hypot(0.4 - 0.41, 0, 0)),
    ("dot", ["return dot(keypoints[1], keypoints[1])"], 0.25 + 0.01 + 0.04),
    ("cross", ["c = cross(R_control_tcp[:, 0], R_control_tcp[:, 1])", "return c[2]"], 1.0),
    ("sqrt", ["return sqrt(params[1])"], math.sqrt(0.5)),
    ("sqrt of zero", ["return sqrt(params[0] - params[0])"], 0.0),
    ("sin", ["return sin(params[1])"], math.sin(0.5)),
    ("cos", ["return cos(params[2])"], math.cos(-1.5)),
    ("acos", ["return acos(params[1])"], math.acos(0.5)),
    ("acos at bounds", ["return acos(1) + acos(-1)"], math.pi),
    ("abs", ["return abs(params[2])"], 1.5),
    ("minimum", ["return minimum(params[0], params[2])"], -1.5),
    ("maximum", ["return maximum(params[0], params[2])"], 0.02),
    ("clip inside", ["return clip(params[1], 0, 1)"], 0.5),
    ("clip low", ["return clip(params[2], -1, 1)"], -1.0),
    ("clip high", ["return clip(3, -1, 1)"], 1.0),
    ("clip with equal bounds", ["return clip(params[1], 0.25, 0.25)"], 0.25),
    ("local reassignment", ["x = params[1]", "x = x * 2", "return x"], 1.0),
    ("unused arguments", ["return 0"], 0.0),
    ("comments and blank lines", ["# a comment", "", "x = 1  # trailing", "return x"], 1.0),
    ("negative result", ["return -0.25"], -0.25),
    ("nested calls", ["return sqrt(abs(minimum(params[2], -4)))"], 2.0),
]


@pytest.mark.parametrize("name,lines,expected", ACCEPTED, ids=[case[0] for case in ACCEPTED])
def test_accepted_language(name, lines, expected):
    result = run(body(*lines), R=M)
    assert type(result) is float
    assert math.isclose(result, expected, rel_tol=1e-12, abs_tol=1e-15)


def test_empty_bindings_are_allowed_when_unused():
    program = compiled(body("return norm(p_tcp)"), keypoints=0, axes=0, params=0)
    assert program.evaluate([3, 4, 0], IDENTITY, [], [], []) == 5.0
    assert program.evaluate((3, 4, 0), IDENTITY, (), (), ()) == 5.0


def test_empty_feature_columns_have_zero_length_but_cannot_be_returned():
    assert rejection(body("return keypoints[:, 0]"), keypoints=0).code == "non_scalar_return"
    program = compiled(body("c = keypoints[:, 0] * 2", "return 1"), keypoints=0)
    assert program.evaluate(P, IDENTITY, [], AXES, PARAMS) == 1.0


def test_one_line_function_and_crlf_source_are_accepted():
    assert run(HEADER.replace("\n", " return params[1]\n")) == 0.5
    assert run(body("x = params[1]", "return x").replace("\n", "\r\n")) == 0.5


# Rejected language ----------------------------------------------------------------

REJECTED = [
    # Imports, I/O, dynamic execution, external lookups and recursion (G04).
    ("import", ["import math", "return 0"], "forbidden_syntax"),
    ("from import", ["from os import path", "return 0"], "forbidden_syntax"),
    ("print", ["print(p_tcp)", "return 0"], "forbidden_syntax"),
    ("print in expression", ["x = print(1)", "return 0"], "forbidden_call"),
    ("open", ["return open('x')"], "forbidden_call"),
    ("open without string", ["return open(1)"], "forbidden_call"),
    ("eval", ["return eval(1)"], "forbidden_call"),
    ("exec", ["x = exec(1)", "return 0"], "forbidden_call"),
    ("dunder import", ["x = __import__(1)", "return 0"], "forbidden_call"),
    ("builtin len", ["return len(p_tcp)"], "forbidden_call"),
    ("builtin min", ["return min(1, 2)"], "forbidden_call"),
    ("globals", ["x = globals", "return 0"], "undefined_name"),
    ("module name", ["return math.pi"], "forbidden_syntax"),
    ("numpy name", ["return np"], "undefined_name"),
    ("undefined name", ["return value"], "undefined_name"),
    ("use before assignment", ["x = y", "y = 1", "return x"], "undefined_name"),
    ("bare primitive reference", ["f = norm", "return 0"], "undefined_name"),
    ("recursion", ["return evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params)"], "forbidden_call"),
    ("attribute call", ["return p_tcp.sum()"], "forbidden_call"),
    ("attribute access", ["return R_control_tcp.T"], "forbidden_syntax"),
    ("called expression", ["return (norm)(p_tcp)"], None),  # (norm)(x) is a direct call in the AST
    ("keyword arguments", ["return clip(params[0], lo=0, hi=1)"], "forbidden_call"),
    ("starred arguments", ["return norm(*p_tcp)"], "forbidden_syntax"),
    # Control flow and other statements.
    ("for loop", ["for x in p_tcp:", "    pass", "return 0"], "forbidden_syntax"),
    ("while loop", ["while 1:", "    pass", "return 0"], "forbidden_syntax"),
    ("if statement", ["if params[0]:", "    return 1", "return 0"], "forbidden_syntax"),
    ("conditional expression", ["return 1 if params[0] else 0"], "forbidden_syntax"),
    ("comparison", ["return params[0] < 1"], "forbidden_syntax"),
    ("boolean operation", ["return params[0] and params[1]"], "forbidden_syntax"),
    ("not operator", ["return not params[0]"], "forbidden_syntax"),
    ("invert operator", ["return ~params[0]"], "forbidden_syntax"),
    ("try", ["try:", "    x = 1", "except Exception:", "    x = 2", "return x"], "forbidden_syntax"),
    ("with", ["with params:", "    pass", "return 0"], "forbidden_syntax"),
    ("raise", ["raise ValueError", "return 0"], "forbidden_syntax"),
    ("assert", ["assert params[0]", "return 0"], "forbidden_syntax"),
    ("pass", ["pass", "return 0"], "forbidden_syntax"),
    ("global", ["global x", "return 0"], "forbidden_syntax"),
    ("docstring", ["'''doc'''", "return 0"], "forbidden_syntax"),
    ("expression statement", ["norm(p_tcp)", "return 0"], "forbidden_syntax"),
    ("nested function", ["def helper(x):", "    return x", "return 0"], "forbidden_syntax"),
    ("lambda", ["f = lambda x: x", "return 0"], "forbidden_syntax"),
    ("class", ["class X:", "    pass", "return 0"], "forbidden_syntax"),
    ("match", ["match params:", "    case _:", "        pass", "return 0"], "forbidden_syntax"),
    ("del local", ["x = 1", "del x", "return 0"], "forbidden_syntax"),
    ("augmented local", ["x = 1", "x += 1", "return x"], "forbidden_syntax"),
    ("annotated local", ["x: float = 1", "return x"], "forbidden_syntax"),
    ("walrus", ["return (x := 1)"], "forbidden_syntax"),
    ("chained assignment", ["x = y = 1", "return x"], "forbidden_syntax"),
    ("tuple unpacking", ["x, y = 1, 2", "return x"], "forbidden_syntax"),
    ("local item assignment", ["v = p_tcp * 1", "v[0] = 1", "return 0"], "forbidden_syntax"),
    # Input rebinding and mutation.
    ("rebind input", ["p_tcp = keypoints[0]", "return 0"], "input_rebinding"),
    ("rebind params", ["params = 1", "return 0"], "input_rebinding"),
    ("augmented input", ["params += 1", "return 0"], "input_rebinding"),
    ("annotated input", ["params: float = 1", "return 0"], "input_rebinding"),
    ("delete input", ["del p_tcp", "return 0"], "input_rebinding"),
    ("walrus input", ["return (params := 1)"], "input_rebinding"),
    ("unpack into input", ["x, p_tcp = 1, 2", "return x"], "input_rebinding"),
    ("starred unpack into input", ["x, *p_tcp = 1, 2, 3", "return x"], "input_rebinding"),
    ("item assignment", ["p_tcp[0] = 1", "return 0"], "input_mutation"),
    ("nested item assignment", ["R_control_tcp[0][1] = 1", "return 0"], "input_mutation"),
    ("augmented item", ["params[0] += 1", "return 0"], "input_mutation"),
    ("delete item", ["del keypoints[0]", "return 0"], "input_mutation"),
    ("attribute assignment", ["axis_dirs.flags = 1", "return 0"], "input_mutation"),
    ("rebind primitive", ["norm = 1", "return 0"], "reserved_name"),
    ("rebind entrypoint", ["evaluate = 1", "return 0"], "reserved_name"),
    # Literals and containers.
    ("string", ["return 'text'"], "invalid_literal"),
    ("bytes", ["return b'x'"], "invalid_literal"),
    ("boolean", ["return True"], "invalid_literal"),
    ("none", ["return None"], "invalid_literal"),
    ("complex", ["return 1j"], "invalid_literal"),
    ("ellipsis", ["return ..."], "invalid_literal"),
    ("infinite literal", ["return 1e400"], "invalid_literal"),
    ("huge integer literal", ["return " + "9" * 400], "invalid_literal"),
    ("f-string", ["return f'{params}'"], "forbidden_syntax"),
    ("list", ["return [1]"], "forbidden_syntax"),
    ("tuple", ["x = (1, 2)", "return 0"], "forbidden_syntax"),
    ("dict", ["x = {}", "return 0"], "forbidden_syntax"),
    ("set", ["x = {1}", "return 0"], "forbidden_syntax"),
    ("comprehension", ["x = [v for v in p_tcp]", "return 0"], "forbidden_syntax"),
    ("generator", ["return norm(v for v in p_tcp)"], "forbidden_syntax"),
    # Operators and shapes.
    ("power", ["return params[0] ** 2"], "forbidden_syntax"),
    ("modulo", ["return params[0] % 2"], "forbidden_syntax"),
    ("floor division", ["return params[0] // 2"], "forbidden_syntax"),
    ("matrix multiply", ["v = R_control_tcp @ p_tcp", "return 0"], "forbidden_syntax"),
    ("vector and matrix", ["v = p_tcp - keypoints", "return 0"], "shape_mismatch"),
    ("matrix and vector", ["v = R_control_tcp * p_tcp", "return 0"], "shape_mismatch"),
    ("different feature counts", ["v = keypoints + axis_dirs", "return 0"], None),
    ("vector return", ["return p_tcp"], "non_scalar_return"),
    ("matrix return", ["return R_control_tcp"], "non_scalar_return"),
    ("column return", ["return R_control_tcp[:, 0]"], "non_scalar_return"),
    ("cross return", ["return cross(p_tcp, p_tcp)"], "non_scalar_return"),
    # Primitive signatures.
    ("norm of scalar", ["return norm(1)"], "shape_mismatch"),
    ("norm of matrix", ["return norm(keypoints)"], "shape_mismatch"),
    ("norm of column", ["return norm(keypoints[:, 0])"], "shape_mismatch"),
    ("dot with scalar", ["return dot(p_tcp, 1)"], "shape_mismatch"),
    ("sqrt of vector", ["return sqrt(p_tcp)"], "shape_mismatch"),
    ("minimum of vectors", ["return minimum(p_tcp, p_tcp)"], "shape_mismatch"),
    ("clip of vector", ["return clip(p_tcp, 0, 1)"], "shape_mismatch"),
    ("norm arity", ["return norm(p_tcp, p_tcp)"], "arity"),
    ("cross arity", ["return cross(p_tcp)[0]"], "arity"),
    ("clip arity", ["return clip(1, 2)"], "arity"),
    ("sin arity", ["return sin()"], "arity"),
    # Fixed indexing and bounds.
    ("vector index out of bounds", ["return p_tcp[3]"], "index_out_of_bounds"),
    ("matrix row out of bounds", ["return R_control_tcp[3][0]"], "index_out_of_bounds"),
    ("matrix column out of bounds", ["return R_control_tcp[0, 3]"], "index_out_of_bounds"),
    ("feature index out of bounds", ["return keypoints[2][0]"], "index_out_of_bounds"),
    ("axis index out of bounds", ["return axis_dirs[2][0]"], "index_out_of_bounds"),
    ("param index out of bounds", ["return params[3]"], "index_out_of_bounds"),
    ("huge index", ["return params[" + "9" * 30 + "]"], "index_out_of_bounds"),
    ("negative index", ["return p_tcp[-1]"], "invalid_index"),
    ("data-dependent index", ["i = 0", "return p_tcp[i]"], "invalid_index"),
    ("computed index", ["return p_tcp[1 + 1]"], "invalid_index"),
    ("float index", ["return p_tcp[0.0]"], "invalid_index"),
    ("boolean index", ["return p_tcp[True]"], "invalid_index"),
    ("partial slice", ["v = p_tcp[0:2]", "return 0"], "invalid_index"),
    ("open slice", ["v = p_tcp[1:]", "return 0"], "invalid_index"),
    ("slice step", ["v = p_tcp[::2]", "return 0"], "invalid_index"),
    ("ellipsis index", ["v = p_tcp[...]", "return 0"], "invalid_index"),
    ("too many indices", ["return p_tcp[0, 0]"], "invalid_index"),
    ("index a scalar", ["return params[0][0]"], "invalid_index"),
    ("index a literal", ["return 1[0]"], "invalid_index"),
    ("empty index tuple", ["return p_tcp[()]"], "invalid_index"),
    ("starred index", ["return p_tcp[*params]"], "invalid_index"),
    # Function body structure.
    ("missing return", ["x = 1"], "function_structure"),
    ("bare return", ["return"], "function_structure"),
    ("early return", ["return 1", "x = 2", "return x"], "function_structure"),
    ("two returns", ["return 1", "return 2"], "function_structure"),
    ("yield", ["yield 1", "return 0"], "forbidden_syntax"),
    ("await", ["x = await params", "return 0"], "forbidden_syntax"),
]


@pytest.mark.parametrize("name,lines,code", REJECTED, ids=[case[0] for case in REJECTED])
def test_rejected_language(name, lines, code):
    source = body(*lines)
    if name == "called expression":
        assert run(source) == math.hypot(*P)
        return
    if name == "different feature counts":
        assert rejection(source, axes=1).code == "shape_mismatch"
        assert run(source) == 0.0  # equal fixed shapes are elementwise-compatible
        return
    error = rejection(source)
    assert error.code == code, error
    assert error.path.startswith("$.source_code:")


@pytest.mark.parametrize("header,code", [
    ("def main(p_tcp, R_control_tcp, keypoints, axis_dirs, params):", "signature"),
    ("def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs):", "signature"),
    ("def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params, extra):", "signature"),
    ("def evaluate(R_control_tcp, p_tcp, keypoints, axis_dirs, params):", "signature"),
    ("def evaluate(p, R, K, A, theta):", "signature"),
    ("def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params=1):", "signature"),
    ("def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, *params):", "signature"),
    ("def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, **params):", "signature"),
    ("def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, *, params):", "signature"),
    ("def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params, /):", "signature"),
    ("def evaluate(p_tcp: list, R_control_tcp, keypoints, axis_dirs, params):", "signature"),
    ("def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params) -> float:", "signature"),
    ("def evaluate[T](p_tcp, R_control_tcp, keypoints, axis_dirs, params):", "signature"),
    ("@decorator\ndef evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params):", "signature"),
    ("async def evaluate(p_tcp, R_control_tcp, keypoints, axis_dirs, params):", "function_structure"),
])
def test_function_signature_must_match_the_abi(header, code):
    assert rejection(header + "\n    return 0\n").code == code


@pytest.mark.parametrize("source", [
    "",
    "# only a comment\n",
    "x = 1\n",
    "import math\n" + body("return 0"),
    body("return 0") + "x = 1\n",
    body("return 0") + body("return 1"),
    "class evaluate:\n    pass\n",
    "evaluate = lambda p_tcp, R_control_tcp, keypoints, axis_dirs, params: 0\n",
    'if __name__ == "__main__":\n    pass\n' + body("return 0"),
])
def test_source_must_contain_exactly_one_function(source):
    assert rejection(source).code == "function_structure"


@pytest.mark.parametrize("source", [
    HEADER + "    return (\n",
    HEADER + "return 0\n",
    "def evaluate(:\n",
    HEADER + "    x = " + "(" * 250 + "1" + ")" * 250 + "\n    return x\n",
    HEADER + "    return 1\x00\n",
])
def test_syntax_errors_are_rejected(source):
    error = rejection(source)
    assert error.code == "syntax_error"
    assert error.path.startswith("$.source_code")


def test_error_locations_are_one_based_and_bounded():
    # A decimal literal beyond CPython's digit limit fails in the parser.
    error = rejection(body("return " + "1" * 5000))
    assert error.code == "syntax_error"
    line, column = map(int, error.path.removeprefix("$.source_code:").split(":"))
    assert line == 2 and column >= 1
    # Limit violations at context/operator nodes report the nearest located node.
    for lines in (["return p_tcp" + ".a" * 40], ["return " + "-" * 40 + "params[0]"]):
        error = rejection(body(*lines))
        assert error.code == "ast_limit" and error.path.startswith("$.source_code:2:"), error


def test_huge_hex_index_is_a_stable_bounded_rejection():
    # Hex literals avoid the parser's decimal digit limit, but the int is still too large to format.
    for digits in (3500, 3600):
        source = body("return params[0x" + "f" * digits + "]")
        assert len(source.encode()) <= NUMERIC_OFFLINE_V1.max_source_bytes
        error = rejection(source)
        assert error.code == "index_out_of_bounds"
        assert len(str(error)) < 200


# Artifact and policy limits ---------------------------------------------------------

def test_source_size_is_rejected_before_parsing():
    limit = NUMERIC_OFFLINE_V1.max_source_bytes
    at_limit = body("return 0") + "#" * 0
    at_limit += "#" * (limit - len(at_limit.encode()))
    assert len(at_limit.encode()) == limit
    assert compiled(at_limit).evaluate(P, IDENTITY, KEYPOINTS, AXES, PARAMS) == 0.0
    oversized = "def evaluate(:\n" + "#" * limit  # invalid syntax, but size wins
    error = rejection(oversized)
    assert (error.code, error.path) == ("source_limit", "$.source_code")
    multibyte = body("return 0") + "#" + "é" * (limit // 2)
    assert rejection(multibyte).code == "source_limit"


def test_assignment_count_limit():
    accepted = body(*[f"x{i} = {i}" for i in range(64)], "return x63")
    assert compiled(accepted).evaluate(P, IDENTITY, KEYPOINTS, AXES, PARAMS) == 63.0
    error = rejection(body(*[f"x{i} = {i}" for i in range(65)], "return 0"))
    assert error.code == "ast_limit" and "assignments" in error.message


def test_ast_node_and_depth_limits():
    # Wide and shallow: 40 short assignments exceed 512 nodes but not depth 32.
    many = rejection(body(*[f"x{i} = params[0] * params[1] - params[2]" for i in range(40)], "return 0"))
    assert many.code == "ast_limit" and "nodes" in many.message
    assert compiled(body(*[f"x{i} = params[0] * params[1] - params[2]" for i in range(10)], "return x9"))
    deep = rejection(body("return " + "-" * 40 + "params[0]"))
    assert deep.code == "ast_limit" and "depth" in deep.message
    # A pathological but small source is bounded iteratively, without recursion errors.
    assert rejection(body("return " + "-" * 4000 + "1")).code == "ast_limit"
    # CPython's own parser limit is reported as a syntax rejection, not a crash.
    overflow = body("return " + "-" * 8000 + "1")
    assert len(overflow.encode()) <= NUMERIC_OFFLINE_V1.max_source_bytes
    assert rejection(overflow).code == "syntax_error"
    tight = ValidationPolicy("tight", 1e-8, 1e-8, 8192, 8, 32, 32, 32, max_ast_nodes=20)
    assert rejection(body("return params[0] + params[1]"), policy=tight).code == "ast_limit"


def test_array_element_limit_uses_static_shapes():
    small = ValidationPolicy("small-arrays", 1e-8, 1e-8, 8192, 8, 32, 32, 32, max_array_elements=8)
    assert compile_program(artifact(body("return norm(p_tcp)")), policy=small)
    error = rejection(body("return R_control_tcp[0][0]"), policy=small)
    assert error.code == "array_limit"


def test_hash_mismatch_and_invalid_artifacts_are_rejected():
    item = artifact(body("return 0"))
    item["source_sha256"] = "0" * 64
    error = rejection_of(item)
    assert (error.code, error.path) == ("source_hash_mismatch", "$.source_sha256")
    changed = artifact(body("return 0"))
    changed["source_code"] = body("return 1")
    assert rejection_of(changed).code == "source_hash_mismatch"
    for field, value in [("function_api_version", "geometry-residual-v2"), ("entrypoint", "main"),
                         ("keypoint_bindings", ["kp", "kp"]), ("output_unit", "deg"),
                         ("parameters", [{"name": "x", "value": float("nan"), "unit": "m"}])]:
        invalid = artifact(body("return 0"))
        invalid[field] = value
        error = rejection_of(invalid)
        assert error.code == "invalid_artifact", (field, error)
    surrogate = artifact(body("return 0"))
    surrogate["source_code"] = body("return 0") + "# \ud800\n"
    assert rejection_of(surrogate).code == "invalid_artifact"
    missing = artifact(body("return 0"))
    del missing["source_code"]
    assert rejection_of(missing).code == "invalid_artifact"
    for value in ["def evaluate(): pass", None, 3, [artifact(body("return 0"))]]:
        assert rejection_of(value).code == "invalid_artifact"


def test_directly_constructed_records_are_revalidated():
    record = parse_record(ConstraintProgram, artifact(body("return params[0]"), params=1), policy=NUMERIC_OFFLINE_V1)
    assert compile_program(record, policy=NUMERIC_OFFLINE_V1).evaluate(P, IDENTITY, KEYPOINTS, AXES, [2]) == 2.0
    forged = ConstraintProgram(
        record.program_id, record.source_sha256, "geometry-residual-v1", record.object_id,
        record.description, "evaluate", record.source_code, ("kp", "kp"), (),
        (Parameter("x", 1.0, "m"),), "m")
    assert rejection_of(forged).code == "invalid_artifact"


def test_policy_must_be_explicit():
    with pytest.raises(TypeError):
        compile_program(artifact(body("return 0")), policy="numeric-offline-v1")
    with pytest.raises(ValueError):
        ValidationPolicy("bad", 1e-8, 1e-8, 8192, 8, 32, 32, 32, rotation_tolerance=-1.0)
    with pytest.raises(ValueError):
        ValidationPolicy("bad", 1e-8, 1e-8, 8192, 8, 32, 32, 32, max_ast_depth=0)
    profile = NUMERIC_OFFLINE_V1
    assert (profile.max_ast_nodes, profile.max_ast_depth, profile.max_assignments,
            profile.max_array_elements, profile.rotation_tolerance) == (512, 32, 64, 256, 1e-8)


def rejection_of(item):
    with pytest.raises(InterpreterError) as caught:
        compile_program(item, policy=NUMERIC_OFFLINE_V1)
    assert caught.value.stage == "compile"
    return caught.value


# Inputs -----------------------------------------------------------------------------

class FloatScalar(float):
    """Stands in for array scalar types such as numpy.float64."""


INPUT_CASES = [
    ("p_tcp tuple too short", dict(p=(0.4, 0.0)), "input_shape", "$.p_tcp"),
    ("p_tcp too long", dict(p=(0.4, 0.0, 0.3, 1.0)), "input_shape", "$.p_tcp"),
    ("p_tcp not a sequence", dict(p="abc"), "input_type", "$.p_tcp"),
    ("p_tcp generator", dict(p=(x for x in P)), "input_type", "$.p_tcp"),
    ("p_tcp boolean", dict(p=(True, 0.0, 0.3)), "input_type", "$.p_tcp[0]"),
    ("p_tcp string element", dict(p=("0.4", 0.0, 0.3)), "input_type", "$.p_tcp[0]"),
    ("p_tcp array scalar", dict(p=(FloatScalar(0.4), 0.0, 0.3)), "input_type", "$.p_tcp[0]"),
    ("p_tcp nan", dict(p=(math.nan, 0.0, 0.3)), "non_finite_input", "$.p_tcp[0]"),
    ("p_tcp infinity", dict(p=(0.4, -math.inf, 0.3)), "non_finite_input", "$.p_tcp[1]"),
    ("p_tcp huge integer", dict(p=(10 ** 400, 0, 0)), "non_finite_input", "$.p_tcp[0]"),
    ("rotation shape", dict(R=IDENTITY[:2]), "input_shape", "$.R_control_tcp"),
    ("rotation row shape", dict(R=((1, 0), (0, 1, 0), (0, 0, 1))), "input_shape", "$.R_control_tcp[0]"),
    ("rotation nonfinite", dict(R=((math.nan, 0, 0), (0, 1, 0), (0, 0, 1))), "non_finite_input",
     "$.R_control_tcp[0][0]"),
    ("rotation scaled", dict(R=((2, 0, 0), (0, 2, 0), (0, 0, 2))), "invalid_rotation", "$.R_control_tcp"),
    ("rotation zero", dict(R=((0, 0, 0), (0, 0, 0), (0, 0, 0))), "invalid_rotation", "$.R_control_tcp"),
    ("rotation reflection", dict(R=((1, 0, 0), (0, 1, 0), (0, 0, -1))), "invalid_rotation", "$.R_control_tcp"),
    ("rotation shear", dict(R=((1, 0.1, 0), (0, 1, 0), (0, 0, 1))), "invalid_rotation", "$.R_control_tcp"),
    ("rotation beyond tolerance", dict(R=((1 + 1e-7, 0, 0), (0, 1, 0), (0, 0, 1))), "invalid_rotation",
     "$.R_control_tcp"),
    ("rotation overflow", dict(R=((1e200, 0, 0), (0, 1e200, 0), (0, 0, 1))), "invalid_rotation",
     "$.R_control_tcp"),
    ("keypoint count", dict(keypoints=KEYPOINTS[:1]), "input_shape", "$.keypoints"),
    ("keypoint row", dict(keypoints=((0.4, 0.0), KEYPOINTS[1])), "input_shape", "$.keypoints[0]"),
    ("keypoint nonfinite", dict(keypoints=(KEYPOINTS[0], (0.1, math.inf, 0.0))), "non_finite_input",
     "$.keypoints[1][1]"),
    ("axis count", dict(axes=AXES + ((0.0, 1.0, 0.0),)), "input_shape", "$.axis_dirs"),
    ("axis zero", dict(axes=((0.0, 0.0, 0.0), AXES[1])), "non_unit_axis", "$.axis_dirs[0]"),
    ("axis non-unit", dict(axes=(AXES[0], (0.6, 0.6, 0.0))), "non_unit_axis", "$.axis_dirs[1]"),
    ("axis beyond tolerance", dict(axes=((1 + 2e-8, 0, 0), AXES[1])), "non_unit_axis", "$.axis_dirs[0]"),
    ("params count", dict(params=PARAMS[:2]), "input_shape", "$.params"),
    ("params nested", dict(params=((1.0,), 0.5, -1.5)), "input_type", "$.params[0]"),
    ("params type", dict(params={"a": 1}), "input_type", "$.params"),
]


@pytest.mark.parametrize("name,inputs,code,path", INPUT_CASES, ids=[case[0] for case in INPUT_CASES])
def test_invalid_inputs_are_rejected_without_repair(name, inputs, code, path):
    error = failure(body("return 0"), stage="input", **inputs)
    assert (error.code, error.path) == (code, path), error


@pytest.mark.parametrize("R", [
    rotation_z(0.3),
    ((1 + 1e-9, 0, 0), (0, 1, 0), (0, 0, 1)),
    tuple(tuple(x for x in row) for row in ((0, -1, 0), (1, 0, 0), (0, 0, 1))),
    [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
])
def test_valid_rotations_within_policy_tolerance_are_accepted_unchanged(R):
    assert run(body("return R_control_tcp[0][0]"), R=R) == float(R[0][0])


@pytest.mark.parametrize("axis", [(1 + 5e-9, 0.0, 0.0), (0.6, 0.8, 0.0), [0, 0, -1]])
def test_unit_axes_within_tolerance_are_accepted_unchanged(axis):
    assert run(body("return axis_dirs[0][0]"), axes=(axis, AXES[1])) == float(axis[0])


def test_unused_binding_arrays_are_still_checked():
    error = failure(body("return 0"), stage="input", axes=((0.0, 0.0, 0.0), AXES[1]))
    assert error.code == "non_unit_axis"


# Numerical failures (G06) -------------------------------------------------------------

EVALUATION_FAILURES = [
    ("scalar division by zero", ["return params[1] / (params[0] - params[0])"], "division_by_zero"),
    ("division by negative zero", ["return 1 / -(params[0] - params[0])"], "division_by_zero"),
    ("zero divided by zero", ["z = params[0] - params[0]", "return z / z"], "division_by_zero"),
    ("vector division with one zero element", ["v = keypoints[0] / p_tcp", "return 0"], "division_by_zero"),
    ("scalar divided by vector", ["v = 1 / p_tcp", "return 0"], "division_by_zero"),
    ("sqrt of negative", ["return sqrt(params[2])"], "domain_error"),
    ("sqrt of tiny negative", ["return sqrt(-1e-300)"], "domain_error"),
    ("acos above one", ["return acos(1 + 1e-15)"], "domain_error"),
    ("acos below minus one", ["return acos(params[2])"], "domain_error"),
    ("clip with reversed bounds", ["return clip(params[0], 1, 0)"], "domain_error"),
    ("multiplication overflow", ["return 1e200 * 1e200"], "non_finite"),
    ("addition overflow", ["return 1.7e308 + 1.7e308"], "non_finite"),
    ("subtraction overflow", ["return -1.7e308 - 1.7e308"], "non_finite"),
    ("division overflow", ["return 1e300 / 1e-300"], "non_finite"),
    ("vector overflow", ["v = p_tcp * 1e308 * 1e308", "return 0"], "non_finite"),
    ("norm overflow", ["return norm(p_tcp * 1e308 + 1e308)"], "non_finite"),
    ("dot overflow", ["v = p_tcp * 0 + 1e200", "return dot(v, v)"], "non_finite"),
    ("cross overflow", ["v = p_tcp * 0 + 1e200", "w = cross(v, v + 1e200)", "return 0"], "non_finite"),
    ("unused overflowing local", ["x = 1e308 * 10", "return 0"], "non_finite"),
]


@pytest.mark.parametrize("name,lines,code", EVALUATION_FAILURES, ids=[case[0] for case in EVALUATION_FAILURES])
def test_numerical_failures_are_explicit(name, lines, code):
    p = (0.4, 0.0, 0.3)
    error = failure(body(*lines), p=p)
    assert error.code == code, error
    assert error.path.startswith("$.source_code:")


def test_numerical_edge_values_that_remain_finite_succeed():
    assert run(body("return 1e-200 * 1e-200")) == 0.0
    assert math.copysign(1.0, run(body("return sqrt(-0.0)"))) == -1.0
    assert run(body("return norm(p_tcp * 0 + 1e307)")) == pytest.approx(math.sqrt(3) * 1e307)
    assert run(body("return cos(1e300) * 0")) == 0.0
    assert run(body("return acos(clip(1 + 1e-12, -1, 1))")) == 0.0


def test_failure_never_produces_a_residual_and_is_pickle_safe():
    program = compiled(body("return 1 / (p_tcp[0] - 0.4)"))
    with pytest.raises(InterpreterError) as caught:
        program.evaluate((0.4, 0, 0), IDENTITY, KEYPOINTS, AXES, PARAMS)
    restored = pickle.loads(pickle.dumps(caught.value))
    assert type(restored) is InterpreterError
    assert (restored.stage, restored.path, restored.code) == ("evaluation", "$.source_code:2:12", "division_by_zero")
    assert isinstance(restored, ValueError)
    # The same program remains usable for valid inputs afterwards.
    assert program.evaluate((0.5, 0, 0), IDENTITY, KEYPOINTS, AXES, PARAMS) == pytest.approx(10.0)


# Immutability, determinism and isolation from Python execution --------------------------

def test_evaluation_does_not_modify_or_retain_inputs():
    program = compiled(body("v = p_tcp - keypoints[0]", "return norm(v) + params[0]"))
    p = [0.4, 0.0, 0.3]
    rotation = [list(row) for row in IDENTITY]
    keypoints = [list(row) for row in KEYPOINTS]
    axes = [list(row) for row in AXES]
    params = list(PARAMS)
    originals = deepcopy((p, rotation, keypoints, axes, params))
    first = program.evaluate(p, rotation, keypoints, axes, params)
    assert (p, rotation, keypoints, axes, params) == originals
    keypoints[0][0] = 10.0
    params[0] = 5.0
    changed = program.evaluate(p, rotation, keypoints, axes, params)
    keypoints[0][0], params[0] = originals[2][0][0], originals[4][0]
    assert program.evaluate(p, rotation, keypoints, axes, params).hex() == first.hex()
    assert changed != first


def test_evaluation_is_deterministic_and_independent_of_earlier_calls():
    source = wiki_programs()["program-bottle-axis-v1"]
    first, second = (compile_program(source, policy=NUMERIC_OFFLINE_V1) for _ in range(2))
    a = first.evaluate(P, rotation_z(0.2), [], [(0.0, 0.6, 0.8)], [0.3])
    for angle in (0.1, 1.0, -2.0):
        first.evaluate(P, rotation_z(angle), [], [(1.0, 0.0, 0.0)], [0.1])
    assert first.evaluate(P, rotation_z(0.2), [], [(0.0, 0.6, 0.8)], [0.3]).hex() == a.hex()
    assert second.evaluate(P, rotation_z(0.2), [], [(0.0, 0.6, 0.8)], [0.3]).hex() == a.hex()


def test_compiled_program_is_immutable_and_detached_from_the_source_mapping():
    item = artifact(body("return params[0]"), params=1)
    program = compile_program(item, policy=NUMERIC_OFFLINE_V1)
    item["source_code"] = body("return 99")
    item["parameters"][0]["value"] = 7.0
    assert program.evaluate(P, IDENTITY, KEYPOINTS, AXES, [3.0]) == 3.0
    assert program.program.parameters[0].value == 1.0
    assert type(program.program.keypoint_bindings) is tuple
    with pytest.raises(FrozenInstanceError):
        program.policy = None
    assert isinstance(program, CompiledProgram)
    assert "_steps" not in repr(program)


def test_python_eval_and_exec_are_never_used(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Python eval/exec must not be used")

    monkeypatch.setattr(builtins, "eval", forbidden)
    monkeypatch.setattr(builtins, "exec", forbidden)
    for item in wiki_programs().values():
        program = compile_program(item, policy=NUMERIC_OFFLINE_V1)
        features = len(item["keypoint_bindings"]) * [P], len(item["axis_bindings"]) * [(1.0, 0.0, 0.0)]
        result = program.evaluate(P, IDENTITY, *features, [p["value"] for p in item["parameters"]])
        assert math.isfinite(result)


def test_generated_source_cannot_reach_host_objects():
    for name in ("__builtins__", "__import__", "globals", "locals", "vars", "getattr", "compile",
                 "open", "input", "breakpoint", "type", "object", "math", "ssc_core", "time", "random"):
        error = rejection(body(f"x = {name}", "return 0"))
        assert error.code == "undefined_name", (name, error)
        error = rejection(body(f"return {name}(1)"))
        assert error.code == "forbidden_call", (name, error)
