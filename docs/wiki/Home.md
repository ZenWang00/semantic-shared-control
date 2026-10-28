# Candidate-point Shared Control

The current contract is schema 2.0, introduced by [Issue #33](https://github.com/ZenWang00/semantic-shared-control/issues/33). It replaces generated numerical programs with data-only candidate TCP positions. The offline prototype combines current XY joystick requests, task relevance and grounded candidate points, selects one target, and proposes bounded XYZ steps while preserving measured orientation.

This is position assistance in a synthetic static scene. It does not establish live VLM perception, 6D grasp assistance, real-arm kinematics, task success or hardware stopping performance.

## Reading order

- [System Architecture and Scope](System-Architecture-and-Scope)
- [Candidate Point Contract](Candidate-Point-Contract)
- [Data Model and Message Contracts](Data-Model-and-Message-Contracts)
- [Offline Candidate Profile](Offline-Candidate-Profile)
- [Runtime Lifecycle and Timing](Runtime-Lifecycle-and-Timing)
- [Validation and Legacy Integration](Validation-and-Legacy-Integration)

## Development

[Development Workflow](Development-Workflow), [Issue Standards](Issue-Standards), [Project Board](Project-Board) and [Repository Ownership](Repository-Ownership-and-Cross-Repository-Workflow) govern both repositories. Shared algorithms and archival belong to this repository; the runtime, synthetic plant and final command authority belong to Grasp-Pose-Generation. [Project #3](https://github.com/users/ZenWang00/projects/3) tracks delivery.

Historical P1 experiments remain historical evidence in Git and retained artifacts. New schema-2 software does not reinterpret or replay schema-1 generated-program archives. The inherited independent grasp service remains outside this offline controller.
