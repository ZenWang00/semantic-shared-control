"""Bounded short-horizon TCP optimization (solver-slsqp-v1); no motion authority.

This host module is standard-library only. It validates one immutable snapshot,
its matching ObjectWeights and the accepted-command reference, then hands a
plain numerical problem to a supervised solver process (``_solver_worker``).
That process runs SciPy SLSQP under a 1 GiB address-space limit and obtains
every generated residual from its own separately spawned evaluator children.
The host enforces the real monotonic deadline, tears down the whole process
group on timeout or failure, independently rechecks hard margins at the emitted
target and returns a ControlResult proposal. A move is only a proposal: the
robot-side gate must still check authority, freshness and host constraints.
"""
from concurrent.futures import Future
from dataclasses import dataclass
import importlib
import json
import math
import os
import platform
import re
import select
import signal
import subprocess
import sys
import threading
import time
from typing import Literal
from uuid import uuid4

from . import records as r
from .fusion import IntentConfig, estimate_intent, get_intent_config
from .host_constraints import HostConstraintResult, MeasuredStateView
from .penalties import HostNormalization, residual_diagnostic
from .policy import NUMERIC_OFFLINE_V1
from .registry import ResolvedBundle, ResolvedSnapshot
from .supervision import _decode, _encode
from .validation import parse_record, to_dict

SolverStatus = Literal["solved", "infeasible", "timeout", "error", "not_run"]
_BLAS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
         "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS")
_SPEC = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True, slots=True)
class SolverConfig:
    """Immutable solver-slsqp-v1 content; archive it together with its ID."""

    solver_config_id: str
    method: str
    smoothing_coefficient: float
    eps: float
    ftol: float
    maxiter: int
    variable_bound: float
    solve_budget_s: float
    max_programs: int
    address_space_bytes: int
    payload_bytes: int
    preparation_timeout_s: float
    teardown_grace_s: float


def get_solver_config(solver_config_id: str = "solver-slsqp-v1") -> SolverConfig:
    """Return a fresh copy of the only approved configuration; others are rejected."""
    if solver_config_id != "solver-slsqp-v1":
        raise ValueError(f"unsupported solver configuration: {solver_config_id!r}")
    return SolverConfig("solver-slsqp-v1", "SLSQP", 0.1, 1e-6, 1e-9, 100, 1.0, 0.15, 16,
                        1024 * 1024 * 1024, 1024 * 1024, 5.0, 0.1)


@dataclass(frozen=True, slots=True)
class HostCheckerSpec:
    """Trusted host configuration naming a zero-argument checker factory.

    ``factory`` is ``module.path:attribute``; both the host and the solver
    process import it from installed packages. Never build it from a message.
    """

    factory: str

    def __post_init__(self):
        if type(self.factory) is not str or not _SPEC.fullmatch(self.factory):
            raise ValueError("checker factory must be 'module.path:attribute'")


@dataclass(frozen=True, slots=True)
class AppliedCommandReference:
    """Accepted-command bookkeeping for previous_applied_command_id.

    Mirrors the adapter's record: the exact ControlResult message, its accepted
    CommandAcceptanceRecord and, for a move, the ResolvedSnapshot it came from.
    These are accepted target velocities, not measured motion.
    """

    result: r.Message
    acceptance: r.CommandAcceptanceRecord
    source: ResolvedSnapshot | None


@dataclass(frozen=True, slots=True)
class OptimizationRequest:
    """Validated identities plus the exact numerical problem sent to the solver.

    ``problem_json`` is canonical JSON (sorted keys) for archival and replay.
    ``previous_reference`` records which zero/nonzero smoothing rule applied.
    """

    session_id: str
    snapshot_id: str
    weights_id: str
    result_id: str
    control_epoch: int
    command_seq: int
    source_joystick_seq: int
    source_robot_state_seq: int
    dt_s: float
    estimator_config_id: str
    solver_config_id: str
    previous_reference: str
    problem_json: str

    def problem(self) -> dict:
        return json.loads(self.problem_json)


@dataclass(frozen=True, slots=True)
class PreparationOutcome:
    ok: bool
    code: str
    diagnostic: str
    started_ns: int
    deadline_ns: int
    finished_ns: int
    worker_pid: int | None
    worker_generation: str | None
    evaluator_pids: tuple[tuple[str, int], ...]
    program_ids: tuple[str, ...]
    checker_factory: str
    solver_config: SolverConfig
    limits: tuple[tuple[str, tuple[int, int]], ...]
    environment: tuple[tuple[str, str], ...]
    cleanup_s: float
    reaped: bool | None


@dataclass(frozen=True, slots=True)
class OptimizationOutcome:
    """One proposal or reasoned hold with retained numerical evidence.

    ``result`` is a parsed ControlResult (move or hold). ``code`` refines the
    solver status. ``report_json`` holds the worker's increments, objective
    terms, final residuals, margins and SciPy diagnostics when available.
    ``host_margins`` are the host's independent margins at the emitted target.
    Times use the host monotonic clock; solve_time_s is admission to decision.
    """

    result: r.ControlResult
    code: str
    diagnostic: str
    request: OptimizationRequest | None
    report_json: str | None
    evaluator_json: str | None
    host_margins: tuple[tuple[str, float], ...]
    started_ns: int
    deadline_ns: int
    finished_ns: int
    worker_pid: int | None
    worker_generation: str | None
    worker_elapsed_s: float | None
    cleanup_s: float
    reaped: bool | None
    solver_config: SolverConfig
    environment: tuple[tuple[str, str], ...]

    @property
    def elapsed_s(self) -> float:
        return (self.finished_ns - self.started_ns) / 1e9


class OptimizationInputError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _require(condition, code, message):
    if not condition:
        raise OptimizationInputError(code, message)


def _float(value):
    return float(value)


def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def _relative_rotvec(target, source):
    """Principal Log(R_target R_source^T) as a rotation vector, standard library only."""
    x, y, z, w = _qmul(tuple(map(float, target)),
                       (-float(source[0]), -float(source[1]), -float(source[2]), float(source[3])))
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    if w < 0:
        x, y, z, w = -x, -y, -z, -w
    vector = math.sqrt(x * x + y * y + z * z)
    if vector == 0:
        return (0.0, 0.0, 0.0)
    angle = 2.0 * math.atan2(vector, w)
    return (angle * x / vector, angle * y / vector, angle * z / vector)


def basic_step_margins(target: r.Pose, measured: r.Pose, u_xy, *, step_m: float,
                       step_rad: float) -> dict[str, float]:
    """Section 4 margins recomputed from an emitted target, as the gate does."""
    x = tuple((float(a) - float(b)) / step_m for a, b in zip(target.position_xyz, measured.position_xyz))
    rv = tuple(value / step_rad for value in _relative_rotvec(target.quaternion_xyzw, measured.quaternion_xyzw))
    magnitude = math.hypot(*u_xy)
    direction = (u_xy[0] / magnitude, u_xy[1] / magnitude) if magnitude else (0.0, 0.0)
    margins = {"translation_norm": magnitude * magnitude - math.fsum(v * v for v in x),
               "rotation_norm": magnitude * magnitude - math.fsum(v * v for v in rv),
               "input_half_space": direction[0] * x[0] + direction[1] * x[1]}
    for kind, values in (("translation", x), ("rotation", rv)):
        for axis, value in zip("xyz", values):
            margins[f"{kind}_{axis}_lower"] = 1.0 + value
            margins[f"{kind}_{axis}_upper"] = 1.0 - value
    return margins


def _previous_reference(snapshot, session_id, previous, config):
    """Return (kind, x_prev, r_prev); errors for missing or unaccepted references."""
    zero = ([0.0] * 3, [0.0] * 3)
    if snapshot.previous_applied_command_id is None:
        _require(previous is None, "previous_reference", "snapshot has no previous command but one was supplied")
        return ("none",) + zero
    _require(type(previous) is AppliedCommandReference, "previous_reference",
             "previous_applied_command_id requires its accepted-command record")
    _require(type(previous.result) is r.Message and type(previous.acceptance) is r.CommandAcceptanceRecord,
             "previous_reference", "expected a ControlResult message and acceptance record")
    message = previous.result
    acceptance = parse_record(r.CommandAcceptanceRecord, to_dict(previous.acceptance), policy=NUMERIC_OFFLINE_V1)
    _require(message.message_type == "ControlResult" and type(message.payload) is r.ControlResult,
             "previous_reference", "previous record is not a ControlResult")
    _require(message.meta.session_id == session_id, "previous_reference", "previous command is from another session")
    result = parse_record(r.ControlResult, to_dict(message.payload), policy=NUMERIC_OFFLINE_V1)
    _require(acceptance.command_accepted and acceptance.result_id == result.result_id and
             acceptance.applied_command_id == snapshot.previous_applied_command_id,
             "previous_reference", "previous command was not accepted under this applied-command ID")
    _require(result.control_epoch <= snapshot.control_epoch, "previous_reference",
             "previous command is from a later epoch")
    if result.status == "hold":
        return ("hold",) + zero
    if result.control_epoch != snapshot.control_epoch:
        return ("old_epoch",) + zero
    source = previous.source
    _require(type(source) is ResolvedSnapshot and source.snapshot.snapshot_id == result.snapshot_id and
             source.bundle.session_id == session_id and source.robot_state.message_type == "RobotState",
             "previous_reference", "previous move lacks its exact same-session source snapshot")
    _require(result.horizon_s == source.snapshot.dt_s, "previous_reference", "previous horizon differs from its source")
    start, target, dt_prev = source.robot_state.payload.tcp_pose, result.next_tcp_pose, float(result.horizon_s)
    step_m = config.max_linear_speed_mps * float(snapshot.dt_s)
    step_rad = config.max_angular_speed_radps * float(snapshot.dt_s)
    x_prev = [(float(a) - float(b)) / dt_prev * float(snapshot.dt_s) / step_m
              for a, b in zip(target.position_xyz, start.position_xyz)]
    r_prev = [value / dt_prev * float(snapshot.dt_s) / step_rad
              for value in _relative_rotvec(target.quaternion_xyzw, start.quaternion_xyzw)]
    _require(all(math.isfinite(v) for v in x_prev + r_prev), "previous_reference", "nonfinite previous reference")
    return "move", x_prev, r_prev


def build_request(resolved: ResolvedSnapshot, weights: r.ObjectWeights, *, intent_config: IntentConfig,
                  command_seq: int, previous_applied: AppliedCommandReference | None = None,
                  result_id: str | None = None, solver_config: SolverConfig | None = None,
                  prepared_programs: dict | None = None) -> OptimizationRequest:
    """Validate pinned inputs and form the numerical problem; pure and replayable.

    Raises OptimizationInputError with a stable code. Weights must equal the
    profile estimator's output for this exact snapshot (within 1e-12) and
    alpha must equal the configuration. Receipt *trust* remains the activation
    boundary's responsibility; this checks accepted-status metadata only.
    """
    solver_config = solver_config or get_solver_config()
    _require(solver_config == get_solver_config(solver_config.solver_config_id), "invalid_config",
             "solver configuration differs from its immutable definition")
    _require(type(resolved) is ResolvedSnapshot, "input_type", "expected ResolvedSnapshot")
    _require(type(weights) is r.ObjectWeights, "input_type", "expected ObjectWeights")
    _require(type(command_seq) is int and command_seq >= 0, "input_type", "command_seq must be a host counter")
    try:
        expected_config = get_intent_config(intent_config.estimator_config_id)
    except (AttributeError, ValueError) as exc:
        raise OptimizationInputError("invalid_config", str(exc)) from None
    _require(type(intent_config) is IntentConfig and intent_config == expected_config, "invalid_config",
             "intent configuration differs from its immutable definition")
    weights = parse_record(r.ObjectWeights, to_dict(weights), policy=NUMERIC_OFFLINE_V1)
    recomputed = estimate_intent(resolved, intent_config, weights_id=weights.weights_id, policy=NUMERIC_OFFLINE_V1)
    if recomputed.disposition == "error":
        raise OptimizationInputError(f"invalid_input_{recomputed.code}", recomputed.diagnostic)
    if recomputed.disposition == "disabled":
        raise OptimizationInputError("input_not_active", "neutral or disabled input uses the local hold path")
    expected = recomputed.weights
    _require(weights.snapshot_id == expected.snapshot_id and
             weights.estimator_config_id == expected.estimator_config_id and
             weights.assistance_strength == expected.assistance_strength and
             [w.object_id for w in weights.weights] == [w.object_id for w in expected.weights] and
             all(abs(a.weight - b.weight) <= NUMERIC_OFFLINE_V1.weight_sum_tolerance
                 for a, b in zip(weights.weights, expected.weights)),
             "weights_mismatch", "ObjectWeights differ from the estimator output for this snapshot")

    bundle, snapshot = resolved.bundle, resolved.snapshot
    config = bundle.configuration
    _require(float(snapshot.dt_s) == float(config.control_period_s) and snapshot.dt_s > 0,
             "dt_mismatch", "snapshot dt_s must equal the session control period")
    joystick, state = resolved.joystick.payload, resolved.robot_state.payload
    _require((state.frame_id, state.tcp_frame) == (config.control_frame, config.tcp_frame),
             "frame_mismatch", "robot state frames differ from the session")
    programs = {item.program.program_id: item for item in bundle.programs}
    _require(len(programs) == len(bundle.programs) and 0 < len(programs) <= solver_config.max_programs,
             "program_count", "expected 1-16 uniquely identified programs")
    _require({(ref.program_id, ref.source_sha256) for ref in snapshot.program_refs} ==
             {(p.program.program_id, p.program.source_sha256) for p in programs.values()},
             "snapshot_reference", "snapshot programs differ from the resolved bundle")
    bindings = {binding.program_id: binding for binding in bundle.execution_bindings}
    _require(len(bindings) == len(bundle.execution_bindings) == len(programs) and bindings.keys() == programs.keys(),
             "binding_coverage", "expected exactly one execution binding per program")
    _require({b.binding_id for b in bindings.values()} == set(snapshot.execution_binding_refs),
             "snapshot_reference", "snapshot binding references differ from the bundle")
    receipts = {receipt.receipt_id: receipt for receipt in bundle.receipts}
    weight_of = {item.object_id: float(item.weight) for item in weights.weights}
    terms = []
    for program_id in sorted(programs):
        resolved_program, binding = programs[program_id], bindings[program_id]
        program = resolved_program.program
        receipt = receipts.get(binding.validation_receipt_id)
        _require(binding.config_id == snapshot.config_id, "binding_reference", "binding configuration differs")
        _require(receipt is not None and receipt.status == "accepted" and receipt.program_id == program_id and
                 receipt.source_sha256 == program.source_sha256, "binding_reference",
                 "binding lacks an accepted receipt for this exact program")
        _require(program.object_id == resolved_program.candidate.object_id and program.object_id in weight_of,
                 "candidate_reference", "program candidate is not a weighted semantic candidate")
        if prepared_programs is not None:
            _require(prepared_programs.get(program_id) == to_dict(program), "context_mismatch",
                     "snapshot program differs from the prepared solver context")
        terms.append({"program_id": program_id, "object_id": program.object_id,
                      "object_weight": weight_of[program.object_id], "binding_weight": _float(binding.weight),
                      "scale": _float(binding.normalization_scale), "output_unit": program.output_unit,
                      "keypoints": [[_float(v) for v in point] for point in resolved_program.keypoints],
                      "axis_dirs": [[_float(v) for v in axis] for axis in resolved_program.axis_dirs],
                      "params": [_float(v) for v in resolved_program.params]})
    if prepared_programs is not None:
        _require(set(prepared_programs) == set(programs), "context_mismatch",
                 "snapshot programs differ from the prepared solver context")
    kind, x_prev, r_prev = _previous_reference(snapshot, bundle.session_id, previous_applied, config)
    dt = float(snapshot.dt_s)
    step_m, step_rad = float(config.max_linear_speed_mps) * dt, float(config.max_angular_speed_radps) * dt
    _require(step_m > 0 and step_rad > 0, "invalid_config", "step scales must be positive")
    problem = {
        "dt_s": dt, "L_m": step_m, "A_rad": step_rad,
        "xy_gain": float(config.max_xy_speed_mps) * dt / step_m,
        "u_xy": [_float(v) for v in joystick.u_xy], "alpha": _float(weights.assistance_strength),
        "smoothing": solver_config.smoothing_coefficient,
        "p_tcp": [_float(v) for v in state.tcp_pose.position_xyz],
        "q_tcp": [_float(v) for v in state.tcp_pose.quaternion_xyzw],
        "x_prev": x_prev, "r_prev": r_prev, "terms": terms,
        "measured_state": {"frame_id": state.frame_id, "tcp_frame": state.tcp_frame,
                           "tcp_pose": to_dict(state.tcp_pose), "joint_names": list(state.joint_names),
                           "joint_positions_rad": [_float(v) for v in state.joint_positions_rad]},
        "configuration": to_dict(config),
        "hard_margin_tolerance": NUMERIC_OFFLINE_V1.hard_margin_tolerance,
    }
    text = json.dumps(problem, sort_keys=True, allow_nan=False, separators=(",", ":"))
    return OptimizationRequest(bundle.session_id, snapshot.snapshot_id, weights.weights_id,
                               result_id or uuid4().hex, snapshot.control_epoch, command_seq,
                               resolved.joystick.meta.sequence, resolved.robot_state.meta.sequence, dt,
                               intent_config.estimator_config_id, solver_config.solver_config_id, kind, text)


def _load_checker(spec: HostCheckerSpec):
    module_name, _, attribute = spec.factory.partition(":")
    checker = getattr(importlib.import_module(module_name), attribute)()
    if not callable(checker):
        raise TypeError("checker factory did not return a callable")
    return checker


class _Failure(Exception):
    def __init__(self, status, code, diagnostic, *, evaluation_valid=False):
        super().__init__(diagnostic)
        self.status, self.code, self.diagnostic = status, code, diagnostic
        self.evaluation_valid = evaluation_valid


def _hold_result(identity, status, code, diagnostic, *, evaluation_valid, solve_time_s):
    data = {"result_id": identity["result_id"], "snapshot_id": identity.get("snapshot_id"),
            "weights_id": identity.get("weights_id"), "control_epoch": identity["control_epoch"],
            "command_seq": identity["command_seq"],
            "source_joystick_seq": identity.get("source_joystick_seq"),
            "source_robot_state_seq": identity.get("source_robot_state_seq"),
            "status": "hold", "evaluation_valid": evaluation_valid, "solver_status": status,
            "constraints_satisfied": None, "solve_time_s": 0 if status == "not_run" else solve_time_s,
            "constraint_evaluations": [], "reason": f"{code}: {diagnostic}"[:512].rstrip() or code}
    return parse_record(r.ControlResult, data, policy=NUMERIC_OFFLINE_V1)


def _finite_list(value, length):
    return (type(value) is list and len(value) == length and
            all(type(v) in (int, float) and math.isfinite(v) for v in value))


class SupervisedOptimizer:
    """One prepared solver process; one in-flight solve; no hidden warm start.

    ``prepare(bundle, motion_disabled=True)`` imports the trusted checker, starts
    a fresh solver process group and prepares one evaluator per bound program.
    ``solve(...)`` validates inputs synchronously, then returns a Future that
    always completes with a parsed ControlResult (move or hold). It never waits
    in the caller beyond validation. A timeout, error or protocol fault retires
    the process group; prepare again while motion is disabled. ``infeasible``
    and ``solved`` keep it ready. Never wait on these Futures in input, release
    or gate callbacks.
    """

    def __init__(self, checker: HostCheckerSpec, *, solver_config: SolverConfig | None = None):
        if type(checker) is not HostCheckerSpec:
            raise TypeError("expected a trusted HostCheckerSpec")
        self._config = solver_config or get_solver_config()
        if self._config != get_solver_config(self._config.solver_config_id):
            raise ValueError("solver configuration differs from its immutable definition")
        self._checker_spec = checker
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._active = None
        self._closed = False
        self._ready = False
        self._process = None
        self._generation = None
        self._programs = None
        self._host_checker = None
        self._environment = tuple(sorted({
            "system": platform.system(), "release": platform.release(),
            "machine": platform.machine(), "python": platform.python_version(),
            "implementation": platform.python_implementation()}.items()))

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._ready and self._active is None and not self._closed

    @property
    def solver_config(self) -> SolverConfig:
        return self._config

    # ----- public operations -------------------------------------------------
    def prepare(self, bundle: ResolvedBundle, *, motion_disabled) -> Future:
        started = time.monotonic_ns()
        deadline = started + int(self._config.preparation_timeout_s * 1e9)
        future = Future()
        future.set_running_or_notify_cancel()
        code = None
        if type(bundle) is not ResolvedBundle:
            code = "invalid_request"
        elif motion_disabled is not True:
            code = "motion_not_disabled"
        with self._lock:
            if self._active is not None:
                code = "busy"
            elif self._closed:
                code = "closed"
            if code:
                future.set_result(self._preparation(False, code, "", started, deadline))
                return future
            self._active, self._ready = future, False
            self._cancel.clear()
        programs = {item.program.program_id: to_dict(item.program) for item in bundle.programs}
        self._run(self._prepare, future, programs, started, deadline)
        return future

    def solve(self, resolved: ResolvedSnapshot, weights: r.ObjectWeights, *, intent_config: IntentConfig,
              command_seq: int, previous_applied: AppliedCommandReference | None = None,
              result_id: str | None = None, deadline_ns: int | None = None) -> Future:
        """Validate, then solve asynchronously before min(start + 150 ms, deadline_ns).

        ``deadline_ns`` is an absolute ``time.monotonic_ns`` bound (for example the
        snapshot's original expiry mapped to real time); it can only shorten the
        budget. Structural misuse (wrong snapshot type or command counter) raises.
        """
        started = time.monotonic_ns()
        if type(resolved) is not ResolvedSnapshot:
            raise TypeError("expected ResolvedSnapshot")
        if type(command_seq) is not int or command_seq < 0:
            raise ValueError("command_seq must be a nonnegative host counter")
        if result_id is not None and (type(result_id) is not str or not result_id):
            raise TypeError("result_id must be a nonempty string or None")
        deadline = started + int(self._config.solve_budget_s * 1e9)
        invalid_deadline = deadline_ns is not None and (type(deadline_ns) is not int or deadline_ns < 0)
        if deadline_ns is not None and not invalid_deadline:
            deadline = min(deadline, deadline_ns)
        snapshot = resolved.snapshot
        identity = {"result_id": result_id or uuid4().hex, "control_epoch": snapshot.control_epoch,
                    "command_seq": command_seq, "snapshot_id": snapshot.snapshot_id,
                    "weights_id": getattr(weights, "weights_id", None),
                    "source_joystick_seq": resolved.joystick.meta.sequence,
                    "source_robot_state_seq": resolved.robot_state.meta.sequence}
        future = Future()
        future.set_running_or_notify_cancel()
        request = None
        try:
            if invalid_deadline:
                raise OptimizationInputError("invalid_deadline", "deadline_ns must be a monotonic integer")
            with self._lock:
                programs = self._programs if self._ready else None
            request = build_request(resolved, weights, intent_config=intent_config, command_seq=command_seq,
                                    previous_applied=previous_applied, result_id=identity["result_id"],
                                    solver_config=self._config, prepared_programs=programs)
        except OptimizationInputError as exc:
            future.set_result(self._outcome(identity, "not_run", exc.code, str(exc), started, deadline,
                                            evaluation_valid=False))
            return future
        except Exception as exc:  # Corrupt records are invalid input, never a solve.
            future.set_result(self._outcome(identity, "not_run", "invalid_input", type(exc).__name__,
                                            started, deadline, evaluation_valid=False))
            return future
        with self._lock:
            code = None
            if self._active is not None:
                code = "busy"
            elif self._closed:
                code = "closed"
            elif not self._ready or self._programs is None or self._programs is not programs:
                code = "not_prepared"  # includes a context re-prepared during validation
            if code:
                future.set_result(self._outcome(identity, "not_run", code, "solver unavailable", started,
                                                deadline, request=request, evaluation_valid=False))
                return future
            self._active, self._ready = future, False
            self._cancel.clear()
        self._run(self._solve, future, (identity, request), started, deadline)
        return future

    def close(self) -> Future:
        """Withdraw uncommitted work and retire the process group; final.

        An operation commits its outcome and clears ``_active`` under the same
        lock as close. Future delivery/callbacks follow outside that lock. A
        close after commit returns a separate retirement Future and does not
        change the committed outcome, even if its delivery is still pending.
        """
        with self._lock:
            self._closed, self._ready = True, False
            self._cancel.set()
            if self._active is not None:
                return self._active
            future = Future()
            future.set_running_or_notify_cancel()
            self._active = future
        def finish():
            try:
                cleanup, reaped = self._retire(graceful=True)
            except Exception:
                cleanup, reaped = 0., False
            with self._lock:
                self._active = None
            now = time.monotonic_ns()
            future.set_result(self._preparation(reaped, "closed" if reaped else "cleanup_failed", "",
                                                now, now, cleanup=cleanup, reaped=reaped))
        try:
            threading.Thread(target=finish, name="ssc-optimizer-close", daemon=True).start()
        except RuntimeError:
            with self._lock:
                self._active = None
            now = time.monotonic_ns()
            future.set_result(self._preparation(False, "supervisor_error", "cannot start close thread",
                                                now, now, reaped=self._process is None))
        return future

    # ----- supervision -------------------------------------------------------
    def _run(self, target, future, payload, started, deadline):
        thread = threading.Thread(target=target, args=(future, payload, started, deadline),
                                  name="ssc-optimizer-supervisor", daemon=True)
        try:
            thread.start()
        except RuntimeError:
            with self._lock:
                self._active, self._ready = None, False
            if target == self._prepare:
                future.set_result(self._preparation(False, "supervisor_error", "cannot start thread",
                                                    started, deadline))
            else:
                identity, request = payload
                future.set_result(self._outcome(identity, "error", "supervisor_error", "cannot start thread",
                                                started, deadline, request=request))

    def _spawn(self):
        environment = os.environ.copy()
        for key in _BLAS:
            environment[key] = "1"
        self._process = subprocess.Popen(
            [sys.executable, "-I", "-m", "ssc_core._solver_worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0, close_fds=True, env=environment, start_new_session=True)
        os.set_blocking(self._process.stdin.fileno(), False)
        os.set_blocking(self._process.stdout.fileno(), False)

    def _check(self, deadline):
        if self._cancel.is_set():
            raise _Failure("timeout", "cancelled", "solver withdrawn by close")
        if time.monotonic_ns() >= deadline:
            raise _Failure("timeout", "timeout", "original monotonic deadline expired")

    def _exchange(self, frame, request_id, deadline):
        process = self._process
        read_fd, write_fd = process.stdout.fileno(), process.stdin.fileno()
        sent, response = 0, bytearray()
        limit = self._config.payload_bytes
        while True:
            self._check(deadline)
            if process.poll() is not None:
                raise _Failure("error", "worker_crash", f"solver exited with code {process.returncode}")
            timeout = min(0.001, max(0, (deadline - time.monotonic_ns()) / 1e9))
            readers, writers, _ = select.select([read_fd], [write_fd] if sent < len(frame) else [], [], timeout)
            if writers:
                try:
                    sent += os.write(write_fd, frame[sent:sent + 65536])
                except BlockingIOError:
                    pass
            if readers:
                try:
                    chunk = os.read(read_fd, min(65536, limit + 2 - len(response)))
                except BlockingIOError:
                    continue
                if not chunk:
                    raise _Failure("error", "worker_crash", "solver closed its reply stream")
                response.extend(chunk)
                if len(response) > limit + 1:
                    raise _Failure("error", "malformed_response", "reply exceeds IPC ceiling")
                if b"\n" in response:
                    if not response.endswith(b"\n") or response.count(b"\n") != 1:
                        raise _Failure("error", "malformed_response", "reply contains extra frames")
                    self._check(deadline)
                    try:
                        reply = _decode(response)
                    except (ValueError, UnicodeError, RecursionError):
                        raise _Failure("error", "malformed_response", "reply is not bounded valid JSON") from None
                    if (type(reply) is not dict or reply.get("request_id") != request_id
                            or reply.get("generation") != self._generation):
                        raise _Failure("error", "malformed_response", "reply identity mismatch")
                    if reply.get("limits") != {"address_space": [self._config.address_space_bytes] * 2,
                                               "core": [0, 0]}:
                        raise _Failure("error", "malformed_response", "invalid resource-limit evidence")
                    elapsed = reply.get("worker_elapsed_s")
                    if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
                        raise _Failure("error", "malformed_response", "invalid worker timing")
                    return reply

    def _retire(self, *, graceful=False):
        """Retire the solver process group and reap its leader.

        Closing the pipes is an EOF request: a responsive worker closes (and
        reaps) its own evaluators, then exits. ``graceful`` waits up to the
        teardown grace for that orderly exit; it is used only when the worker
        replied or motion is disabled. Otherwise, or if it does not exit, the
        whole group is terminated, killed after the grace, and the leader is
        reaped. Evaluators orphaned by a killed or crashed worker are killed
        with the group and reaped by the system init process.
        """
        started = time.monotonic_ns()
        process = self._process
        if process is None:
            return 0., True
        for stream in (process.stdin, process.stdout):
            if stream and not stream.closed:
                stream.close()
        group, grace = process.pid, self._config.teardown_grace_s
        exited = False
        if graceful:
            try:
                process.wait(timeout=grace)
                exited = True
            except subprocess.TimeoutExpired:
                pass
        for sig in (() if exited else (signal.SIGTERM, signal.SIGKILL)):
            try:
                os.killpg(group, sig)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                process.wait(timeout=grace)
                break
            except subprocess.TimeoutExpired:
                continue
        try:
            # Remove any evaluator left running in the group after the leader.
            os.killpg(group, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            process.wait(timeout=grace)
            reaped = True
            self._process = None
        except subprocess.TimeoutExpired:
            reaped = False
        return (time.monotonic_ns() - started) / 1e9, reaped

    def _prepare(self, future, programs, started, deadline):
        ok, code, diagnostic, reply = False, "ok", "", None
        cleanup, reaped = 0., True
        try:
            self._check(deadline)
            cleanup, reaped = self._retire(graceful=True)
            if not reaped:
                raise _Failure("error", "cleanup_failed", "old solver has not been reaped")
            if sys.platform != "linux":
                raise _Failure("error", "unsupported_platform", "solver-slsqp-v1 isolation requires Linux RLIMIT_AS")
            self._generation, self._programs = uuid4().hex, None
            try:
                self._host_checker = _load_checker(self._checker_spec)
            except Exception as exc:
                raise _Failure("error", "checker_unavailable", type(exc).__name__) from None
            request_id = uuid4().hex
            frame = _encode({"operation": "prepare", "request_id": request_id, "generation": self._generation,
                             "deadline_ns": deadline, "solver_config_id": self._config.solver_config_id,
                             "checker": self._checker_spec.factory,
                             "programs": [programs[key] for key in sorted(programs)]})
            self._check(deadline)
            self._spawn()
            reply = self._exchange(frame, request_id, deadline)
            if reply.get("status") != "ready":
                raise _Failure("error", str(reply.get("code"))[:64], str(reply.get("diagnostic"))[:400])
            pids = reply.get("evaluator_pids")
            if type(pids) is not dict or set(pids) != set(programs) or not all(type(v) is int for v in pids.values()):
                raise _Failure("error", "malformed_response", "evaluator preparation evidence is incomplete")
            self._check(deadline)
            ok = True
        except _Failure as exc:
            code, diagnostic = exc.code, exc.diagnostic
        except Exception as exc:
            code, diagnostic = "supervisor_error", type(exc).__name__
        retired = False
        while True:
            with self._lock:
                if self._closed:
                    ok, code, diagnostic = False, "cancelled", "closed before publication"
                elif ok and time.monotonic_ns() >= deadline:
                    ok, code, diagnostic = False, "preparation_deadline", "deadline expired before publication"
                if ok or retired:
                    # The final guard and state commit are one operation with
                    # respect to close; callbacks must run after unlocking.
                    self._ready = ok
                    self._programs = programs if ok else None
                    outcome = self._preparation(ok, code if ok or reaped else "cleanup_failed", diagnostic, started,
                                                deadline, reply=reply, programs=programs, cleanup=cleanup,
                                                reaped=None if ok else reaped)
                    self._active = None
                    break
            try:
                # Retire outside the lock so close/input callers never wait for
                # teardown. Recheck cancellation before committing the outcome.
                elapsed, reaped = self._retire(graceful=reply is not None)
                cleanup += elapsed
            except Exception:
                reaped = False
            retired = True  # one bounded attempt, even if reaping failed
        future.set_result(outcome)

    def _solve(self, future, payload, started, deadline):
        identity, request = payload
        problem = request.problem()
        status, code, diagnostic, evaluation_valid = "error", "supervisor_error", "", False
        reply, result, margins = None, None, ()
        cleanup, reaped = 0., None
        pid = self._process.pid if self._process is not None else None
        try:
            self._check(deadline)
            request_id = uuid4().hex
            frame = _encode({"operation": "solve", "request_id": request_id, "generation": self._generation,
                             "deadline_ns": deadline, "problem": problem})
            reply = self._exchange(frame, request_id, deadline)
            status = reply.get("status")
            if status not in ("solved", "infeasible", "timeout", "error") or type(reply.get("evaluation_valid")) is not bool:
                raise _Failure("error", "malformed_response", "unexpected solver status")
            code, diagnostic = str(reply.get("code"))[:64], str(reply.get("diagnostic") or "")[:400]
            evaluation_valid = reply["evaluation_valid"]
            if status == "solved":
                result, margins = self._move(identity, request, problem, reply.get("report"), started)
                code = "solved"
            self._check(deadline)
        except _Failure as exc:
            status, code, diagnostic = exc.status, exc.code, exc.diagnostic
            evaluation_valid, result = exc.evaluation_valid, None
        except Exception as exc:
            status, code, diagnostic, evaluation_valid, result = "error", "supervisor_error", type(exc).__name__, False, None

        # Serialize diagnostics before the final deadline/cancellation check,
        # not between that check and publication or while holding the lock.
        report_json = json.dumps(reply.get("report"), sort_keys=True) if reply and reply.get("report") is not None else None
        evaluator_json = json.dumps({"failure": reply.get("evaluator"), "stats": reply.get("evaluator_stats")},
                                    sort_keys=True) if reply else None
        retired = False
        while True:
            with self._lock:
                finished = time.monotonic_ns()
                if self._closed:
                    status, code, diagnostic, result = "timeout", "cancelled", "closed before publication", None
                    evaluation_valid = False
                elif finished >= deadline and status in ("solved", "infeasible"):
                    status, code, diagnostic, result = "timeout", "timeout", "deadline expired before publication", None
                    evaluation_valid = False
                # The final status includes host-recheck errors and late
                # cancellation/expiry, not just the worker's original reply.
                retire = status in ("timeout", "error")
                if not retire or retired:
                    if result is None:
                        result = _hold_result(identity, status, code, diagnostic or status,
                                              evaluation_valid=evaluation_valid, solve_time_s=(finished - started) / 1e9)
                    self._ready = not retire
                    if retire:
                        self._programs = None
                    outcome = OptimizationOutcome(
                        result, code, diagnostic, request, report_json, evaluator_json,
                        margins, started, deadline, finished, pid, self._generation,
                        reply.get("worker_elapsed_s") if reply else None, cleanup, reaped if retire else None,
                        self._config, self._environment)
                    # Commit under the same lock as the final guard. A later
                    # close owns a separate retirement; it cannot revoke this
                    # already-decided outcome. Future callbacks run unlocked.
                    self._active = None
                    break
            try:
                # A worker that sent a well-formed reply exits on EOF; others are forced.
                cleanup, reaped = self._retire(graceful=reply is not None and code != "malformed_response")
            except Exception:
                reaped = False
            retired = True  # do not turn a cleanup failure into an unbounded loop
        future.set_result(outcome)

    def _move(self, identity, request, problem, report, started):
        """Recheck the worker's solved report on the host; return (ControlResult, margins)."""
        if type(report) is not dict:
            raise _Failure("error", "malformed_response", "solved reply lacks a report")
        for key, length in (("x", 3), ("r", 3), ("position_xyz", 3), ("quaternion_xyzw", 4)):
            if not _finite_list(report.get(key), length):
                raise _Failure("error", "malformed_response", f"invalid {key}")
        evaluations = report.get("evaluations")
        terms = problem["terms"]
        if (type(evaluations) is not list or [e.get("program_id") for e in evaluations if type(e) is dict]
                != [t["program_id"] for t in terms]):
            raise _Failure("error", "malformed_response", "final evaluations do not cover every program")
        try:
            target = parse_record(r.Pose, {"position_xyz": report["position_xyz"],
                                           "quaternion_xyzw": report["quaternion_xyzw"]}, policy=NUMERIC_OFFLINE_V1)
        except Exception:
            raise _Failure("error", "malformed_response", "emitted pose is not a valid normalized pose") from None
        state = problem["measured_state"]
        measured = parse_record(r.Pose, state["tcp_pose"], policy=NUMERIC_OFFLINE_V1)
        u_xy = tuple(problem["u_xy"])
        tolerance = NUMERIC_OFFLINE_V1.hard_margin_tolerance
        basic = basic_step_margins(target, measured, u_xy, step_m=problem["L_m"], step_rad=problem["A_rad"])
        config = parse_record(r.SessionConfig, problem["configuration"], policy=NUMERIC_OFFLINE_V1)
        view = MeasuredStateView(state["frame_id"], state["tcp_frame"], measured,
                                 tuple(state["joint_names"]), tuple(state["joint_positions_rad"]))
        try:
            host = self._host_checker(candidate_pose=target, measured_state=view, u_xy=u_xy,
                                      dt_s=problem["dt_s"], configuration=config)
            if type(host) is not HostConstraintResult:
                raise TypeError("checker must return HostConstraintResult")
        except Exception as exc:
            raise _Failure("error", "host_checker", type(exc).__name__, evaluation_valid=True) from None
        margins = tuple(sorted(basic.items())) + tuple(sorted((m.name, float(m.value)) for m in host.margins))
        violated = [name for name, value in margins if not value >= -tolerance]
        if violated:
            raise _Failure("infeasible", "host_recheck", ", ".join(violated)[:300], evaluation_valid=True)
        constraint_evaluations = []
        for term, evaluation in zip(terms, evaluations):
            raw = evaluation.get("raw_g")
            try:
                diagnostic = residual_diagnostic(raw, output_unit=term["output_unit"], normalization=HostNormalization(
                    term["output_unit"], term["scale"], term["binding_weight"]))
            except (TypeError, ValueError):
                raise _Failure("error", "malformed_response", "final residual is not a finite scalar") from None
            constraint_evaluations.append({"program_id": term["program_id"], "raw_g": diagnostic.raw_residual,
                                           "normalization_scale": term["scale"], "penalty": diagnostic.penalty})
        satisfied = all(item["raw_g"] <= 1e-9 for item in constraint_evaluations)
        data = {"result_id": identity["result_id"], "snapshot_id": request.snapshot_id,
                "weights_id": request.weights_id, "control_epoch": request.control_epoch,
                "command_seq": request.command_seq, "source_joystick_seq": request.source_joystick_seq,
                "source_robot_state_seq": request.source_robot_state_seq, "status": "move",
                "evaluation_valid": True, "solver_status": "solved", "constraints_satisfied": satisfied,
                "solve_time_s": (time.monotonic_ns() - started) / 1e9,
                "constraint_evaluations": constraint_evaluations,
                "next_tcp_pose": to_dict(target), "horizon_s": request.dt_s}
        return parse_record(r.ControlResult, data, policy=NUMERIC_OFFLINE_V1), margins

    # ----- outcome helpers ---------------------------------------------------
    def _outcome(self, identity, status, code, diagnostic, started, deadline, *, request=None,
                 evaluation_valid=False):
        finished = time.monotonic_ns()
        result = _hold_result(identity, status, code, diagnostic, evaluation_valid=evaluation_valid,
                              solve_time_s=(finished - started) / 1e9)
        return OptimizationOutcome(result, code, diagnostic, request, None, None, (), started, deadline,
                                   finished, None, self._generation, None, 0., None, self._config,
                                   self._environment)

    def _preparation(self, ok, code, diagnostic, started, deadline, *, reply=None, programs=None,
                     cleanup=0., reaped=None):
        pids = reply.get("evaluator_pids") if reply else None
        environment = dict(self._environment)
        if reply and type(reply.get("environment")) is dict:
            environment.update({k: str(v) for k, v in reply["environment"].items()})
        return PreparationOutcome(
            ok, code, diagnostic[:512], started, deadline, time.monotonic_ns(),
            self._process.pid if self._process is not None and ok and code == "ok" else None,
            self._generation if ok and code == "ok" else None,
            tuple(sorted(pids.items())) if type(pids) is dict else (),
            tuple(sorted(programs)) if programs else (), self._checker_spec.factory, self._config,
            (("address_space", (self._config.address_space_bytes,) * 2), ("core", (0, 0))),
            tuple(sorted(environment.items())), cleanup, reaped)


__all__ = [
    "AppliedCommandReference", "HostCheckerSpec", "OptimizationInputError", "OptimizationOutcome",
    "OptimizationRequest", "PreparationOutcome", "SolverConfig", "SupervisedOptimizer",
    "basic_step_margins", "build_request", "get_solver_config",
]
