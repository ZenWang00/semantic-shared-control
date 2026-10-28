# Validation and Legacy Integration

Issue #33 changes the acceptance obligations from generated code to candidate data and fixed position assistance. Existing historical P1 matrices are not evidence for this new contract. The executable current matrix lives in the consumer's `offline/tools/acceptance_matrix.py`; its selectors must refer to passing current tests, including all parameterizations.

## Acceptance groups

| Group | Required evidence |
| --- | --- |
| Schema / inputs | Schema 2.0 only; data-only VLM proposals and 1–16 grounded candidates; correct identity, finite values, types, dimensions, relevance, frames and task/scene/observation references |
| Selection | Same-object alternatives; reorder invariance; deterministic score/distance/ID ordering; strict 0.1 switch threshold; explicit previous selection and reset; no averaged grasp point |
| Position solve | Analytical fixed-distance oracle; high/equal/low targets with the same XY input; strict XY when alpha=0; held non-identity orientation and quaternion sign equivalence |
| Runtime | Atomic activation; stale feedback/input, old context/epoch, deadlines, duplicates, forged results, host rejection, local release and worker stalls; independent plant feedback |
| Archive | Exact inputs and configuration; selector and fixed-distance recomputation; checksum/dependency/tamper rejection; fresh-process replay that cannot command motion; explicit old-format rejection |
| Packaging / CI | Lightweight clean wheel installation, pinned producer/consumer pair, 1/2/16-candidate runs, current matrix, retained manifests and JUnit |

Tests for AST restrictions, generated-code probes, receipts and evaluator process calls are removed with their implementation. Tests of data validation, fixed objectives and solver supervision replace them. Rotation-command tests must not remain as evidence of this position-only profile; orientation representation and preservation remain tested.

## Limits of this evidence

P05 capture-time TF under real camera motion remains deferred to a future adapter. P08 feedback evidence is synthetic only. The offline RGB/depth assets and proposal fixtures do not demonstrate live VLM quality, grounding accuracy, real kinematics, grasp success or physical stopping. Configured periods/deadlines are not real-time performance claims.

## Inherited service

The independent VLM/SAM2/Contact-GraspNet service and lightweight alignment path remain development references, not adapters for this contract. Their grasp convention uses Z approach while the LIO TCP convention uses X approach; a future adapter must explicitly test the frame/axis conversion and gripper offset. A legacy point, score or `GraspDict` must not be relabeled as a current candidate/command without validated identity and geometry conversion.

Future real-robot integration must verify capture timestamps/intrinsics/depth/TF, measured-joint units and FK, host limits, cancellation acknowledgment and observed stopping. None is replaced by synthetic numerical tests.
