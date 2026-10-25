"""Initialization-only numerical validation through the bounded worker.

This module never evaluates generated source in the host. Calls block while
motion is disabled and must not run in input, release or command-gate callbacks.
Probe success is finite numerical evidence, not general semantic correctness.
"""

from dataclasses import dataclass
import math

from .probes import ValidationProbe, validation_probes
from .registry import ResolvedProgram
from .supervision import EvaluationOutcome, SupervisedEvaluator


@dataclass(frozen=True, slots=True)
class ProbeEvidence:
    probe: ValidationProbe
    reversed_axis_id: str | None
    outcome: EvaluationOutcome


@dataclass(frozen=True, slots=True)
class NumericalValidation:
    accepted: bool
    diagnostics: tuple[str, ...]
    preparation: EvaluationOutcome
    probes: tuple[ProbeEvidence, ...]
    cleanup: EvaluationOutcome


def probe_program(resolved: ResolvedProgram, *, motion_disabled: bool) -> NumericalValidation:
    """Run every named pose and each undirected-axis reversal at every pose.

    Only callers that resolved the complete immutable artifact/geometry may use
    this low-level helper. The result alone is not a trusted validation receipt.
    Fresh workers prevent inherited prepared-program or policy state.
    """
    if type(resolved) is not ResolvedProgram:
        raise TypeError("expected a resolved program")
    worker = SupervisedEvaluator()
    evidence, diagnostics = [], []
    try:
        preparation = worker.prepare(resolved.program, motion_disabled=motion_disabled).result()
        if not preparation.ok:
            diagnostics.append(f"prepare/{preparation.code}: {preparation.diagnostic}")
        else:
            axes = {axis.axis_id: axis for axis in resolved.geometry.axes}
            for probe in validation_probes(resolved):
                result = worker.submit(probe.position, probe.rotation, resolved.keypoints,
                                       resolved.axis_dirs, resolved.params).result()
                evidence.append(ProbeEvidence(probe, None, result))
                if not result.ok:
                    diagnostics.append(f"{probe.name}/{result.code}: {result.diagnostic}")
                    break
                for index, axis_id in enumerate(resolved.program.axis_bindings):
                    if axes[axis_id].directed:
                        continue
                    reversed_axes = list(resolved.axis_dirs)
                    reversed_axes[index] = tuple(-v for v in reversed_axes[index])
                    reversed_result = worker.submit(probe.position, probe.rotation, resolved.keypoints,
                                                    reversed_axes, resolved.params).result()
                    evidence.append(ProbeEvidence(probe, axis_id, reversed_result))
                    if not reversed_result.ok:
                        diagnostics.append(f"{probe.name}/reverse/{axis_id}/{reversed_result.code}: "
                                           f"{reversed_result.diagnostic}")
                        break
                    if not math.isclose(result.residual, reversed_result.residual,
                                        abs_tol=1e-9, rel_tol=1e-9):
                        diagnostics.append(f"axis_sign_invariance: {axis_id} at {probe.name}: "
                                           f"{result.residual!r} != {reversed_result.residual!r}")
                        break
                if diagnostics:
                    break
    finally:
        cleanup = worker.close().result()
    if not cleanup.ok or cleanup.reaped is not True:
        diagnostics.append(f"cleanup/{cleanup.code}: worker retirement not confirmed")
    return NumericalValidation(not diagnostics, tuple(diagnostics), preparation, tuple(evidence), cleanup)
