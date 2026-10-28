# Fixed position optimizer

`solver-candidates-v2` optimizes three dimensionless translation values. It evaluates ordinary reviewed host code against the one selected candidate. No source interpreter or evaluator child is created. See the exact objective and bounds in [Offline Candidate Profile](wiki/Offline-Candidate-Profile.md).

The target quaternion is equivalent to the snapshot's measured quaternion. Previous angular command history is ignored. Alpha-zero fixes the Z increment to zero and clears the corresponding smoothing reference. The solver otherwise preserves XY tracking, accepted-command translational smoothing, input-scaled step bounds, the non-opposition half-space and injected host constraints.

`SupervisedOptimizer` retains asynchronous prepare/solve futures, fixed original deadlines, process-group retirement and host-side independent rechecking. Numerical errors, infeasibility, stalled/crashed workers or deadline exhaustion produce a classified hold with no target. No unfinished SLSQP iterate is emitted. The robot-side gate independently checks current authority, feedback and constraints.

Preparation pins candidate data and the trusted checker. Each solve validates its supplied configuration. Rechecks recompute the chosen candidate distance, normalization and fixed penalty; a soft distance residual may remain positive while the intermediate step is acceptable. The next target is not automatically a completed grasp or an instruction to hold after arrival.
