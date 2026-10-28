# System Architecture and Scope

## Inputs and ownership

| Input | Meaning | Current offline source |
| --- | --- | --- |
| Joystick commands | Enabled XY direction and magnitude in the control frame, plus bounded recent history | Scripted samples |
| Task and scene understanding | Task text and per-object task relevance; VLM interface proposes image points as data | Fixed response/parser fixtures and preset relevance |
| Geometry | Candidate TCP positions grounded in a shared control frame | Explicit synthetic numerical targets |
| RobotState | Current measured TCP pose and joints | Independently advanced synthetic plant |

A future preparation adapter consumes VLM image-point proposals, RGB-D, calibration and gripper geometry to produce grounded TCP targets. Image points and raw surface points are not TCP targets. That adapter and live model calls are outside this delivery.

## Control cycle

1. Atomically activate matching task, observation, geometry, semantics, candidate set and configuration.
2. Freeze current joystick/state, history, exact candidate-set identity and previous selection provenance.
3. Score individual candidates, choose one with hysteresis, and record the selection. Multiple alternatives never become an averaged grasp target.
4. Optimize three translation variables against joystick following, smoothing and the selected target's fixed distance objective. Preserve the snapshot's measured orientation.
5. Independently check the proposal at the robot-side gate, then apply a bounded target to the synthetic controller.
6. Sample independent feedback and archive inputs, selection, objective diagnostics, acceptance and observations.

The shared library contains no generated-program interpreter or VLM-controlled function. Fixed numerical functions are maintained and reviewed as ordinary host code. The supervised solver retains its wall deadline and cancellation; the local input/stop path never waits for a solve.

## Scope

The base and scene remain static during an active context. Preparation is separate from the fast input/state loop. Scene/task replacement invalidates old authority and clears selection. User release cancels motion locally; it does not require deleting a still-valid scene. Candidate assistance is soft: reaching a point does not initiate autonomous hold or grasp. The user can continue correcting or moving away.

Real perception, capture-time geometry conversion, moving-object tracking, attached-object motion, gripper actuation and real-robot integration are separate work. An output containing a quaternion does not demonstrate rotational assistance: this profile preserves measured orientation.
