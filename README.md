# Semantic Shared Control

**Status: schema 1.0 record validation, immutable session registries, deterministic reference resolution, the restricted geometry-residual-v1 interpreter, Linux evaluator isolation, host validation receipts, deterministic intent fusion and the supervised bounded short-horizon optimizer implemented; the complete shared-control runtime remains planned.**

This project investigates continuous shared control of a robot end effector from a two-dimensional joystick, task semantics from a vision-language model (VLM), and geometric information represented by keypoints and axes. The proposed optimizer combines these inputs with measured robot state to propose the next short-horizon TCP pose.

The first prototype is scoped to a static scene and a fixed robot base. The operator controls motion in the base XY plane; geometric assistance can influence height and orientation. Gripper actuation, attached-object motion, and autonomous multi-stage tasks are outside this first contract.

## Documentation

- [Install and use the shared package](docs/shared-package.md) — `ssc-core==0.8.0`, strict schema 1.0 records, session registries, reference resolution, the restricted residual interpreter, supervised evaluation, host-reviewed validation receipts and the pure host-constraint interface.
- [Deterministic intent fusion](docs/intent-fusion.md) — snapshot-only relative support, separate assistance strength, immutable configuration and explicit disabled/failure outcomes.
- [Bounded short-horizon optimizer](docs/optimizer.md) — solver-slsqp-v1 in a supervised 1 GiB solver process with per-program evaluators, injected host checker, move proposals or reasoned holds, and retained numerical evidence.

- [Technical documentation on GitHub Wiki](https://github.com/ZenWang00/semantic-shared-control/wiki)
- [Version-controlled documentation source](docs/wiki/Home.md)
- [Architecture](docs/wiki/System-Architecture-and-Scope.md)
- [Data model and message contracts](docs/wiki/Data-Model-and-Message-Contracts.md)
- [Generated constraint program contract](docs/wiki/Generated-Constraint-Program-Contract.md)
- [Runtime lifecycle and timing](docs/wiki/Runtime-Lifecycle-and-Timing.md)
- [Validation and legacy integration](docs/wiki/Validation-and-Legacy-Integration.md)
- [Repository ownership and cross-repository workflow](docs/wiki/Repository-Ownership-and-Cross-Repository-Workflow.md)
- [Offline reference profile and numerical design checks](docs/wiki/Offline-Reference-Profile.md)

The Wiki is a publication copy of `docs/wiki/`. Change the source on a project branch, review it, and synchronize the same Markdown files into the separate Wiki Git repository. The Wiki publication commit records the source project commit.

This repository owns perception/semantic preparation, shared contracts and pure-library validation, fusion/optimization, and offline archival/replay tools. [Grasp-Pose-Generation](https://github.com/ZenWang00/Grasp-Pose-Generation) owns device adapters, robot coordination, final motion authority, and the complete offline loop runner. ROS2 code consumes a versioned shared library from this repository; code ownership does not move the initially robot-local fusion or optimizer to a remote server. Both repositories use this Wiki and the same [development board](https://github.com/users/ZenWang00/projects/3).

## Development policy

**Development Policy v1.1** governs human and AI contributions independently of the technical design draft:

- [Development workflow](docs/wiki/Development-Workflow.md)
- [Issue standards and conversation-sized execution](docs/wiki/Issue-Standards.md)
- [Project board and tracking rules](docs/wiki/Project-Board.md)
- [Live development board](https://github.com/users/ZenWang00/projects/3)
- [Create an issue using a task template](https://github.com/ZenWang00/semantic-shared-control/issues/new/choose)
- [Phase-one Epic and cross-repository execution cards](https://github.com/ZenWang00/semantic-shared-control/issues/2)

Execution issues should fit one working conversation's context, including implementation, verification, and handoff. Split larger outcomes into independently verifiable stages before starting. Repository agents begin with [AGENTS.md](AGENTS.md).

## Implementation baseline

Git history and existing source files are inherited from [ZenWang00/vla-grasp-server](https://github.com/ZenWang00/vla-grasp-server) at commit [`2390315`](https://github.com/ZenWang00/vla-grasp-server/tree/2390315278ad6f734f28a345483f3c144793a5a0). The upstream remote identifies that source repository.

The inherited code implements the earlier VLM/SAM2/Contact-GraspNet grasp service. It **does not implement the new shared-control contracts**. Its endpoints, dependency files, notebooks, reports, and launch scripts are retained as a development baseline; no new runtime behavior is introduced by this documentation revision. For the original operating instructions, see the [baseline README](https://github.com/ZenWang00/vla-grasp-server/blob/2390315278ad6f734f28a345483f3c144793a5a0/README.md).

The companion robot repository is [ZenWang00/Grasp-Pose-Generation](https://github.com/ZenWang00/Grasp-Pose-Generation). Its annotated Git tag [`v1.0.0`](https://github.com/ZenWang00/Grasp-Pose-Generation/tree/v1.0.0) resolves to the inspected commit [`00a42b3`](https://github.com/ZenWang00/Grasp-Pose-Generation/tree/00a42b32ef6d82863d298ccd876d0ee9523afa5d). This is a source baseline; its adapters are not a verified implementation of the proposed stop or timing behavior. The [ownership page](docs/wiki/Repository-Ownership-and-Cross-Repository-Workflow.md) records tag provenance and the version-pair evidence required for future integration.

No control frequency, network deadline, safety property, or robot performance result is claimed by this design draft.
