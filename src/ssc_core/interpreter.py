"""Restricted numerical interpreter for ``geometry-residual-v1`` programs.

``compile_program`` parses generated source with :mod:`ast`, accepts only the
documented straight-line grammar, infers every array shape before execution and
lowers the function body to an immutable expression tree. ``evaluate`` validates
the five ABI inputs and computes one finite float64 residual. Division by zero,
domain errors and nonfinite intermediates fail explicitly; no failure becomes a
residual.

Python ``eval``/``exec`` are never used. Generated source can reach only its
five inputs, its own locals and the listed host primitives: no builtins,
modules, attributes, time, randomness, external references or earlier
evaluations. Language acceptance is not isolation. Process separation, memory
and wall-time budgets and host validation receipts belong to separate layers.
"""

import ast
import math
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, NoReturn, Union

from . import records as r
from .errors import ValidationError
from .policy import ValidationPolicy
from .validation import parse_record, to_dict

INPUT_NAMES = ("p_tcp", "R_control_tcp", "keypoints", "axis_dirs", "params")

_Shape = tuple[int, ...]
_SCALAR: _Shape = ()
_VECTOR3: _Shape = (3,)

# Fixed host primitive signatures: (argument shapes, result shape).
_PRIMITIVES: dict[str, tuple[tuple[_Shape, ...], _Shape]] = {
    "norm": ((_VECTOR3,), _SCALAR),
    "dot": ((_VECTOR3, _VECTOR3), _SCALAR),
    "cross": ((_VECTOR3, _VECTOR3), _VECTOR3),
    "sqrt": ((_SCALAR,), _SCALAR),
    "sin": ((_SCALAR,), _SCALAR),
    "cos": ((_SCALAR,), _SCALAR),
    "acos": ((_SCALAR,), _SCALAR),
    "abs": ((_SCALAR,), _SCALAR),
    "minimum": ((_SCALAR, _SCALAR), _SCALAR),
    "maximum": ((_SCALAR, _SCALAR), _SCALAR),
    "clip": ((_SCALAR, _SCALAR, _SCALAR), _SCALAR),
}
HOST_PRIMITIVES = tuple(_PRIMITIVES)
_OPERATORS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}

_DESCRIPTIONS = {
    ast.Import: "import", ast.ImportFrom: "import", ast.For: "loop", ast.AsyncFor: "loop",
    ast.While: "loop", ast.If: "conditional statement", ast.With: "context manager",
    ast.AsyncWith: "context manager", ast.Try: "exception handling",
    ast.TryStar: "exception handling", ast.Raise: "raise", ast.Assert: "assert",
    ast.Delete: "delete", ast.Global: "scope declaration", ast.Nonlocal: "scope declaration",
    ast.Pass: "pass", ast.Break: "loop control", ast.Continue: "loop control",
    ast.Expr: "expression statement (including a docstring)",
    ast.FunctionDef: "nested function", ast.AsyncFunctionDef: "nested function",
    ast.ClassDef: "class definition", ast.Match: "pattern matching",
    ast.AugAssign: "augmented assignment", ast.AnnAssign: "annotated assignment",
    ast.TypeAlias: "type alias", ast.Attribute: "attribute access",
    ast.Compare: "comparison", ast.BoolOp: "Boolean operation",
    ast.IfExp: "conditional expression", ast.Lambda: "lambda",
    ast.ListComp: "comprehension", ast.SetComp: "comprehension",
    ast.DictComp: "comprehension", ast.GeneratorExp: "comprehension",
    ast.List: "container construction", ast.Tuple: "container construction",
    ast.Set: "container construction", ast.Dict: "container construction",
    ast.NamedExpr: "assignment expression", ast.Starred: "starred expression",
    ast.JoinedStr: "string formatting", ast.FormattedValue: "string formatting",
    ast.Await: "await", ast.Yield: "yield", ast.YieldFrom: "yield",
    ast.Slice: "slice outside an index",
}


class InterpreterError(ValueError):
    """A rejected program, invalid input or failed evaluation.

    ``stage`` is ``"compile"`` (reject the artifact), ``"input"`` (invalid ABI
    arguments) or ``"evaluation"`` (numerical failure). ``code`` is stable;
    ``path`` names the artifact field, input element or ``$.source_code:L:C``
    source location. A failure never yields a residual value.
    """

    def __init__(self, stage: str, path: str, code: str, message: str) -> None:
        self.stage, self.path, self.code, self.message = stage, path, code, message
        super().__init__(f"{stage}: {path}: {code}: {message}")

    def __reduce__(self) -> tuple:
        return type(self), (self.stage, self.path, self.code, self.message)


def _where(node: Any) -> str:
    line = getattr(node, "lineno", None)
    return "$.source_code" if line is None else f"$.source_code:{line}:{node.col_offset + 1}"


def _reject(location: Any, code: str, message: str) -> NoReturn:
    path = location if type(location) is str else _where(location)
    raise InterpreterError("compile", path, code, message)


def _invalid_input(path: str, code: str, message: str) -> NoReturn:
    raise InterpreterError("input", path, code, message)


def _failed(where: str, code: str, message: str) -> NoReturn:
    raise InterpreterError("evaluation", where, code, message)


def _describe(node: ast.AST) -> str:
    return _DESCRIPTIONS.get(type(node), f"{type(node).__name__} syntax")


def _format(shape: _Shape) -> str:
    return "scalar" if not shape else "(" + ", ".join(map(str, shape)) + ("," if len(shape) == 1 else "") + ")"


# Immutable lowered expression tree. Only these node kinds are ever executed.

@dataclass(frozen=True, slots=True)
class _Const:
    value: float


@dataclass(frozen=True, slots=True)
class _Load:
    slot: int


@dataclass(frozen=True, slots=True)
class _Index:
    operand: "_Node"
    spec: tuple[int | None, ...]


@dataclass(frozen=True, slots=True)
class _Negate:
    operand: "_Node"


@dataclass(frozen=True, slots=True)
class _Arithmetic:
    op: str
    left: "_Node"
    right: "_Node"
    where: str


@dataclass(frozen=True, slots=True)
class _Primitive:
    name: str
    args: tuple["_Node", ...]
    where: str


_Node = Union[_Const, _Load, _Index, _Negate, _Arithmetic, _Primitive]


def _input_target(target: ast.AST) -> str | None:
    """Return the violation code when a binding target rebinds or mutates an input."""
    if isinstance(target, ast.Name):
        return "input_rebinding" if target.id in INPUT_NAMES else None
    if isinstance(target, (ast.Subscript, ast.Attribute)):
        base = target
        while isinstance(base, (ast.Subscript, ast.Attribute)):
            base = base.value
        return "input_mutation" if isinstance(base, ast.Name) and base.id in INPUT_NAMES else None
    if isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            code = _input_target(item)
            if code:
                return code
    if isinstance(target, ast.Starred):
        return _input_target(target.value)
    return None


def _check_limits(tree: ast.Module, policy: ValidationPolicy) -> None:
    """Iteratively bound AST size, depth and assignments before any recursion."""
    nodes = assignments = 0
    stack: list[tuple[ast.AST, int]] = [(tree, 1)]
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if nodes > policy.max_ast_nodes:
            _reject(node, "ast_limit", f"source exceeds {policy.max_ast_nodes} AST nodes")
        if depth > policy.max_ast_depth:
            _reject(node, "ast_limit", f"source exceeds AST depth {policy.max_ast_depth}")
        if isinstance(node, ast.Assign):
            assignments += 1
            if assignments > policy.max_assignments:
                _reject(node, "ast_limit", f"source exceeds {policy.max_assignments} assignments")
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))


def _check_signature(function: ast.FunctionDef, entrypoint: str) -> None:
    expected = f"def {entrypoint}({', '.join(INPUT_NAMES)})"
    if function.name != entrypoint:
        _reject(function, "signature", f"expected {expected}")
    if function.decorator_list:
        _reject(function.decorator_list[0], "signature", "decorators are not allowed")
    if function.returns is not None:
        _reject(function.returns, "signature", "annotations are not allowed")
    if getattr(function, "type_params", ()):
        _reject(function, "signature", "type parameters are not allowed")
    arguments = function.args
    names = tuple(argument.arg for argument in arguments.args)
    if (arguments.posonlyargs or arguments.vararg or arguments.kwonlyargs or arguments.kwarg
            or arguments.defaults or arguments.kw_defaults or names != INPUT_NAMES):
        _reject(function, "signature", f"expected exactly {expected} without defaults")
    for argument in arguments.args:
        if argument.annotation is not None:
            _reject(argument, "signature", "annotations are not allowed")


class _Lowering:
    """Statically shape-checked lowering of one accepted function body."""

    def __init__(self, policy: ValidationPolicy, input_shapes: tuple[_Shape, ...]) -> None:
        self.policy = policy
        self.scope: dict[str, tuple[int, _Shape]] = {
            name: (slot, shape) for slot, (name, shape) in enumerate(zip(INPUT_NAMES, input_shapes))
        }
        self.slots = len(INPUT_NAMES)

    def assign(self, statement: ast.Assign) -> tuple[int, _Node]:
        if len(statement.targets) != 1:
            _reject(statement, "forbidden_syntax", "chained assignment is not allowed")
        target = statement.targets[0]
        code = _input_target(target)
        if code:
            _reject(target, code, "inputs are read-only and cannot be rebound or mutated")
        if not isinstance(target, ast.Name):
            _reject(target, "forbidden_syntax", "assignment targets must be local names")
        if target.id in _PRIMITIVES or target.id == "evaluate":
            _reject(target, "reserved_name", f"{target.id!r} is reserved")
        node, shape = self.expression(statement.value)
        slot = self.slots
        self.slots += 1
        self.scope[target.id] = (slot, shape)
        return slot, node

    def expression(self, node: ast.AST) -> tuple[_Node, _Shape]:
        lowered, shape = self._expression(node)
        if shape and math.prod(shape) > self.policy.max_array_elements:
            _reject(node, "array_limit", f"array {_format(shape)} exceeds {self.policy.max_array_elements} elements")
        return lowered, shape

    def _expression(self, node: ast.AST) -> tuple[_Node, _Shape]:
        if isinstance(node, ast.Constant):
            return _Const(self._literal(node)), _SCALAR
        if isinstance(node, ast.Name):
            if node.id in self.scope:
                slot, shape = self.scope[node.id]
                return _Load(slot), shape
            if node.id in _PRIMITIVES:
                _reject(node, "undefined_name", f"host primitive {node.id!r} can only be called directly")
            _reject(node, "undefined_name", f"{node.id!r} is not an input or an assigned local")
        if isinstance(node, ast.BinOp):
            op = _OPERATORS.get(type(node.op))
            if op is None:
                _reject(node, "forbidden_syntax", f"operator {type(node.op).__name__} is not allowed")
            left, left_shape = self.expression(node.left)
            right, right_shape = self.expression(node.right)
            if left_shape and right_shape and left_shape != right_shape:
                _reject(node, "shape_mismatch",
                        f"operands {_format(left_shape)} and {_format(right_shape)} are incompatible")
            return _Arithmetic(op, left, right, _where(node)), left_shape or right_shape
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.UAdd):
                return self.expression(node.operand)
            if isinstance(node.op, ast.USub):
                operand, shape = self.expression(node.operand)
                return _Negate(operand), shape
            _reject(node, "forbidden_syntax", f"operator {type(node.op).__name__} is not allowed")
        if isinstance(node, ast.Call):
            return self._call(node)
        if isinstance(node, ast.Subscript):
            return self._subscript(node)
        if isinstance(node, ast.NamedExpr) and _input_target(node.target):
            _reject(node, "input_rebinding", "inputs are read-only and cannot be rebound")
        _reject(node, "forbidden_syntax", f"{_describe(node)} is not allowed")

    @staticmethod
    def _literal(node: ast.Constant) -> float:
        if type(node.value) not in (int, float):
            _reject(node, "invalid_literal", f"{type(node.value).__name__} literals are not allowed")
        try:
            value = float(node.value)
        except OverflowError:
            value = math.inf
        if not math.isfinite(value):
            _reject(node, "invalid_literal", "numeric literals must be finite float64 values")
        return value

    def _call(self, node: ast.Call) -> tuple[_Node, _Shape]:
        function = node.func
        if not isinstance(function, ast.Name):
            _reject(node, "forbidden_call", "only direct calls to host primitives are allowed")
        if function.id not in _PRIMITIVES:
            reason = "recursion is not allowed" if function.id == "evaluate" else "it is not a host primitive"
            _reject(node, "forbidden_call", f"cannot call {function.id!r}: {reason}")
        if node.keywords:
            _reject(node, "forbidden_call", "keyword arguments are not allowed")
        for argument in node.args:
            if isinstance(argument, ast.Starred):
                _reject(argument, "forbidden_syntax", "starred arguments are not allowed")
        expected, result = _PRIMITIVES[function.id]
        if len(node.args) != len(expected):
            _reject(node, "arity", f"{function.id} takes {len(expected)} positional arguments, got {len(node.args)}")
        arguments = []
        for position, (argument, shape) in enumerate(zip(node.args, expected), start=1):
            lowered, actual = self.expression(argument)
            if actual != shape:
                _reject(argument, "shape_mismatch",
                        f"{function.id} argument {position} must be {_format(shape)}, not {_format(actual)}")
            arguments.append(lowered)
        return _Primitive(function.id, tuple(arguments), _where(node)), result

    def _subscript(self, node: ast.Subscript) -> tuple[_Node, _Shape]:
        operand, shape = self.expression(node.value)
        if not shape:
            _reject(node, "invalid_index", "a scalar cannot be indexed")
        items = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        if not items or len(items) > len(shape):
            _reject(node, "invalid_index", f"expected 1 to {len(shape)} indices for {_format(shape)}")
        spec: list[int | None] = []
        for axis, item in enumerate(items):
            if isinstance(item, ast.Slice):
                if item.lower is not None or item.upper is not None or item.step is not None:
                    _reject(item, "invalid_index", "only full-axis ':' slices are allowed")
                spec.append(None)
            elif isinstance(item, ast.Constant) and type(item.value) is int:
                if item.value >= shape[axis]:
                    _reject(item, "index_out_of_bounds",
                            f"index {item.value} is outside axis {axis} of length {shape[axis]}")
                spec.append(item.value)
            else:
                _reject(item, "invalid_index", "indices must be literal nonnegative integers or ':'")
        result = tuple(size for size, index in zip(shape, spec) if index is None) + shape[len(spec):]
        return _Index(operand, tuple(spec)), result


# Runtime: scalars are float, arrays are nested tuples of float.

def _finite(value: float, where: str) -> float:
    if not math.isfinite(value):
        _failed(where, "non_finite", "intermediate value is not finite")
    return value


def _divide(x: float, y: float, where: str) -> float:
    if y == 0.0:
        _failed(where, "division_by_zero", "division by zero")
    return _finite(x / y, where)


_ARITHMETIC = {
    "+": lambda x, y, where: _finite(x + y, where),
    "-": lambda x, y, where: _finite(x - y, where),
    "*": lambda x, y, where: _finite(x * y, where),
    "/": _divide,
}


def _elementwise(function: Any, left: Any, right: Any, where: str) -> Any:
    if type(left) is float:
        if type(right) is float:
            return function(left, right, where)
        return tuple(_elementwise(function, left, item, where) for item in right)
    if type(right) is float:
        return tuple(_elementwise(function, item, right, where) for item in left)
    return tuple(_elementwise(function, x, y, where) for x, y in zip(left, right, strict=True))


def _negate(value: Any) -> Any:
    return -value if type(value) is float else tuple(_negate(item) for item in value)


def _index(value: tuple, spec: tuple[int | None, ...]) -> Any:
    first, rest = spec[0], spec[1:]
    if first is None:
        return value if not rest else tuple(_index(item, rest) for item in value)
    return value[first] if not rest else _index(value[first], rest)


def _primitive(name: str, args: list, where: str) -> Any:
    mul, add, sub = _ARITHMETIC["*"], _ARITHMETIC["+"], _ARITHMETIC["-"]
    try:
        if name == "norm":
            return _finite(math.hypot(*args[0]), where)
        if name == "dot":
            a, b = args
            return add(add(mul(a[0], b[0], where), mul(a[1], b[1], where), where), mul(a[2], b[2], where), where)
        if name == "cross":
            a, b = args
            return (sub(mul(a[1], b[2], where), mul(a[2], b[1], where), where),
                    sub(mul(a[2], b[0], where), mul(a[0], b[2], where), where),
                    sub(mul(a[0], b[1], where), mul(a[1], b[0], where), where))
        x = args[0]
        if name == "sqrt":
            if x < 0.0:
                _failed(where, "domain_error", "sqrt requires a nonnegative argument")
            return math.sqrt(x)
        if name == "acos":
            if not -1.0 <= x <= 1.0:
                _failed(where, "domain_error", "acos requires an argument in [-1, 1]")
            return math.acos(x)
        if name == "sin":
            return _finite(math.sin(x), where)
        if name == "cos":
            return _finite(math.cos(x), where)
        if name == "abs":
            return abs(x)
        if name == "minimum":
            return min(args[0], args[1])
        if name == "maximum":
            return max(args[0], args[1])
        if name == "clip":
            low, high = args[1], args[2]
            if low > high:
                _failed(where, "domain_error", "clip requires lo <= hi")
            return min(max(x, low), high)
    except OverflowError:
        _failed(where, "non_finite", f"{name} overflowed")
    except ValueError as exc:
        if isinstance(exc, InterpreterError):
            raise
        _failed(where, "domain_error", f"{name} domain error")
    raise AssertionError(f"unsupported primitive {name!r}")


def _run(node: _Node, env: list) -> Any:
    kind = type(node)
    if kind is _Const:
        return node.value
    if kind is _Load:
        return env[node.slot]
    if kind is _Index:
        return _index(_run(node.operand, env), node.spec)
    if kind is _Negate:
        return _negate(_run(node.operand, env))
    if kind is _Arithmetic:
        left = _run(node.left, env)
        right = _run(node.right, env)
        return _elementwise(_ARITHMETIC[node.op], left, right, node.where)
    if kind is _Primitive:
        return _primitive(node.name, [_run(argument, env) for argument in node.args], node.where)
    raise AssertionError(f"unsupported node {kind.__name__}")


# Input conversion: exact list/tuple containers of int/float, copied to floats.

def _number(value: Any, path: str) -> float:
    if type(value) not in (int, float):
        _invalid_input(path, "input_type", "expected an int or float, not a Boolean, string or array scalar")
    try:
        number = float(value)
    except OverflowError:
        number = math.inf
    if not math.isfinite(number):
        _invalid_input(path, "non_finite_input", "expected a finite float64 value")
    return number


def _sequence(value: Any, length: int, path: str) -> list | tuple:
    if type(value) not in (list, tuple):
        _invalid_input(path, "input_type", "expected a list or tuple")
    if len(value) != length:
        _invalid_input(path, "input_shape", f"expected length {length}, got {len(value)}")
    return value


def _vector(value: Any, length: int, path: str) -> tuple[float, ...]:
    return tuple(_number(item, f"{path}[{i}]") for i, item in enumerate(_sequence(value, length, path)))


def _matrix(value: Any, rows: int, path: str) -> tuple[tuple[float, ...], ...]:
    return tuple(_vector(row, 3, f"{path}[{i}]") for i, row in enumerate(_sequence(value, rows, path)))


def _check_rotation(m: tuple[tuple[float, ...], ...], tolerance: float) -> None:
    # Compare R^T R with I and det(R) with 1; never orthonormalize the input.
    for i in range(3):
        for j in range(i, 3):
            product = m[0][i] * m[0][j] + m[1][i] * m[1][j] + m[2][i] * m[2][j]
            if not abs(product - (1.0 if i == j else 0.0)) <= tolerance:
                _invalid_input("$.R_control_tcp", "invalid_rotation",
                               f"columns must be orthonormal within {tolerance}")
    determinant = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                   - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                   + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
    if not abs(determinant - 1.0) <= tolerance:
        _invalid_input("$.R_control_tcp", "invalid_rotation", f"determinant must be 1 within {tolerance}")


@dataclass(frozen=True, slots=True, eq=False)
class CompiledProgram:
    """An accepted artifact lowered to an immutable expression tree.

    Obtain instances only from ``compile_program``. Evaluation keeps no state:
    each call copies and checks its inputs, and results depend only on them.
    """

    program: r.ConstraintProgram
    policy: ValidationPolicy
    _steps: tuple[tuple[int, _Node], ...] = field(repr=False)
    _result: _Node = field(repr=False)
    _slots: int = field(repr=False)

    def evaluate(self, p_tcp: Any, R_control_tcp: Any, keypoints: Any, axis_dirs: Any,
                 params: Any) -> float:
        """Return the finite signed residual ``g``; ``g <= 0`` means satisfied.

        Arrays are lists/tuples of int or float (convert NumPy arrays with
        ``tolist()``). Shapes are (3,), (3, 3), (K, 3), (A, 3) and (P,), with K, A
        and P fixed by the artifact's bindings. Raises InterpreterError.
        """
        env: list[Any] = [None] * self._slots
        env[0] = _vector(p_tcp, 3, "$.p_tcp")
        rotation = _matrix(R_control_tcp, 3, "$.R_control_tcp")
        _check_rotation(rotation, self.policy.rotation_tolerance)
        env[1] = rotation
        env[2] = _matrix(keypoints, len(self.program.keypoint_bindings), "$.keypoints")
        axes = _matrix(axis_dirs, len(self.program.axis_bindings), "$.axis_dirs")
        for i, axis in enumerate(axes):
            if not abs(math.hypot(*axis) - 1.0) <= self.policy.unit_norm_tolerance:
                _invalid_input(f"$.axis_dirs[{i}]", "non_unit_axis",
                               f"axis must have unit norm within {self.policy.unit_norm_tolerance}")
        env[3] = axes
        env[4] = _vector(params, len(self.program.parameters), "$.params")
        for slot, node in self._steps:
            env[slot] = _run(node, env)
        residual = _run(self._result, env)
        if type(residual) is not float or not math.isfinite(residual):
            _failed("$.source_code", "non_finite", "residual must be a finite scalar")
        return residual


def compile_program(program: r.ConstraintProgram | dict, *, policy: ValidationPolicy) -> CompiledProgram:
    """Validate a ``geometry-residual-v1`` artifact and lower its source.

    The artifact is re-parsed under ``policy`` and its exact UTF-8 source hash
    recomputed. Source size is checked before parsing, AST size/depth and
    assignment counts before lowering, and every index bound and array shape
    before any evaluation. Raises InterpreterError with ``stage="compile"``.
    Acceptance does not issue a validation receipt or activate the program.
    """
    if type(policy) is not ValidationPolicy:
        raise TypeError("policy must be an explicit ValidationPolicy")
    try:
        if type(program) is r.ConstraintProgram:
            data = to_dict(program)
        elif type(program) is dict:
            data = program
        else:
            _reject("$", "invalid_artifact", "expected a ConstraintProgram record or mapping")
        source = data.get("source_code")
        if type(source) is str:
            try:
                size = len(source.encode("utf-8"))
            except UnicodeEncodeError:
                size = 0  # Rejected by structural parsing below.
            if size > policy.max_source_bytes:
                _reject("$.source_code", "source_limit", f"source exceeds {policy.max_source_bytes} UTF-8 bytes")
        record = parse_record(r.ConstraintProgram, data, policy=policy)
    except ValidationError as exc:
        raise InterpreterError("compile", exc.path, "invalid_artifact", f"{exc.code}: {exc.message}") from None
    if sha256(record.source_code.encode("utf-8")).hexdigest() != record.source_sha256:
        _reject("$.source_sha256", "source_hash_mismatch", "hash differs from the exact UTF-8 source")
    try:
        tree = ast.parse(record.source_code, filename="<generated-program>", mode="exec")
    except SyntaxError as exc:
        location = f"$.source_code:{exc.lineno}:{exc.offset or 0}" if exc.lineno else "$.source_code"
        _reject(location, "syntax_error", exc.msg)
    except (ValueError, RecursionError, MemoryError) as exc:
        _reject("$.source_code", "syntax_error", f"source cannot be parsed ({type(exc).__name__})")
    _check_limits(tree, policy)
    if len(tree.body) != 1 or type(tree.body[0]) is not ast.FunctionDef:
        offending = next((s for s in tree.body if type(s) is not ast.FunctionDef), None)
        if offending is None and len(tree.body) > 1:
            offending = tree.body[1]
        _reject(offending if offending is not None else "$.source_code", "function_structure",
                "source must contain exactly one function definition and nothing else")
    function = tree.body[0]
    _check_signature(function, record.entrypoint)
    lowering = _Lowering(policy, (
        (3,), (3, 3), (len(record.keypoint_bindings), 3), (len(record.axis_bindings), 3),
        (len(record.parameters),),
    ))
    steps: list[tuple[int, _Node]] = []
    result: _Node | None = None
    for position, statement in enumerate(function.body):
        if isinstance(statement, ast.Return):
            if position != len(function.body) - 1:
                _reject(statement, "function_structure", "return must be the final statement")
            if statement.value is None:
                _reject(statement, "function_structure", "return requires a residual expression")
            result, shape = lowering.expression(statement.value)
            if shape:
                _reject(statement.value, "non_scalar_return", f"return value must be a scalar, not {_format(shape)}")
        elif isinstance(statement, ast.Assign):
            steps.append(lowering.assign(statement))
        else:
            targets = (statement.targets if isinstance(statement, ast.Delete)
                       else [statement.target] if isinstance(statement, (ast.AugAssign, ast.AnnAssign))
                       else [])
            for target in targets:
                code = _input_target(target)
                if code:
                    _reject(target, code, "inputs are read-only and cannot be rebound or mutated")
            _reject(statement, "forbidden_syntax", f"{_describe(statement)} is not allowed")
    if result is None:
        _reject(function.body[-1], "function_structure", "the function must end with one return statement")
    return CompiledProgram(record, policy, tuple(steps), result, lowering.slots)
