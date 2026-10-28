# Candidate-point Shared Control

This project studies continuous XY joystick control assisted by task-relevant candidate TCP positions. The schema-2 offline controller selects one candidate, solves a bounded XYZ step with fixed host formulas, and preserves measured orientation. It consumes data-only candidate proposals; it does not generate or execute VLM-written code.

The current refactor is tracked by [Issue #33](https://github.com/ZenWang00/semantic-shared-control/issues/33). Live model calls, RGB-D grounding, real-arm integration and complete 6D grasp assistance are outside this version. Synthetic test evidence is not hardware or task-benefit evidence.

- [Install the shared package](docs/shared-package.md)
- [Architecture](docs/wiki/System-Architecture-and-Scope.md)
- [Candidate data contract](docs/wiki/Candidate-Point-Contract.md)
- [Schema 2.0](docs/wiki/Data-Model-and-Message-Contracts.md)
- [Offline profile](docs/wiki/Offline-Candidate-Profile.md)
- [Selection](docs/intent-fusion.md), [optimization](docs/optimizer.md), [archive/replay](docs/archive-replay.md)
- [Runtime repository](https://github.com/ZenWang00/Grasp-Pose-Generation)
- [Development workflow](docs/wiki/Development-Workflow.md)

The inherited `vg_pipeline` and `grasp_server` independent grasp service is retained as a separate development baseline. Its APIs and dependencies are not the shared-control package or a live adapter for schema 2. Historical generated-program implementations remain in Git; new software does not carry a compatibility execution path.
