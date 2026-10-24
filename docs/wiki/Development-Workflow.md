# Development Workflow

**Development Policy v1.1 · 24 October 2026**

This policy governs development by the project owner and AI agents in both `ZenWang00/semantic-shared-control` and `ZenWang00/Grasp-Pose-Generation`. It defines how work becomes ready, how changes are reviewed, and what evidence permits completion. It does not change the status of the technical specifications: Design Draft v0.1 remains a proposed design, not a claim of implementation or validation.

Read this page with [Issue Standards](https://github.com/ZenWang00/semantic-shared-control/wiki/Issue-Standards) and [Project Board](https://github.com/ZenWang00/semantic-shared-control/wiki/Project-Board). The reviewed files in `docs/wiki/` are the authoring source; the GitHub Wiki is their published copy. Keep canonical policy changes and server templates consistent in the same pull request; synchronize the ROS2 template copy through a linked PR before accepting a policy update. The shared policy has one authoring source, not separate per-repository versions.

## Responsibilities and decision authority

The project owner chooses research goals and priorities and confirms changes to core design commitments. AI agents may prepare tasks, implement agreed work, verify results, obtain independent review, and maintain task status within that authority.

| Decision or action | Required authority |
| --- | --- |
| Routine implementation, bug fixes, refactoring, and documentation within agreed design and acceptance criteria | The executing agent may proceed, obtain independent review, merge, and close after acceptance. |
| Changes to core message contracts, coordinate frames or units, control authority, stop behavior, or failure semantics | Obtain the project owner's explicit confirmation of the concrete proposed change before adopting it. |
| Running an experiment and collecting or analyzing data under an agreed plan | The executing agent may proceed and retain evidence. |
| Research conclusions intended for the thesis or external communication | Obtain the project owner's confirmation before treating the claim as approved. |

Existing explicit authorization remains valid. Record a durable reference to the approval rather than asking for the same decision again. A technical ambiguity that changes an agreed interface or research question must be resolved before dependent implementation proceeds; independent preparation can continue.

Independent AI review may use the same authenticated GitHub account as the executing agent. It requires a separate agent or review conversation, not a second self-check by the implementer. Do not describe that review as a separate person's GitHub approval, and do not impose a second-account approval requirement on this single-owner workflow.

## From task to accepted result

Every substantive feature, bug fix, experiment, refactor, or documentation change needs an Issue meeting [Issue Standards](https://github.com/ZenWang00/semantic-shared-control/wiki/Issue-Standards). Pure spelling or formatting corrections may use only a PR. The initial policy, template, and board setup has a one-time Issue exemption: one reviewed PR and no placeholder task cards.

| Status | Entry and exit requirements |
| --- | --- |
| **Backlog** | Capture the need and open questions. This status does not authorize implementation. |
| **Ready** | Outcome, scope, dependencies, acceptance criteria, verification, and context size have been checked. Required inputs are available, a real GitHub owner and priority are assigned, and the review path is explicit. |
| **In progress** | An executor claims the Issue, records the working branch and execution scope, and starts the agreed slice. |
| **Review** | Deliverables, self-check evidence, and known limitations are available. Independent review and any required human confirmation can now assess the result. |
| **Done** | Every acceptance criterion is satisfied, independent review is recorded, necessary human decisions are recorded, and repository changes are merged into `main`. |

Keep at most **two Issues across In progress and Review combined across both repositories**. Blocked Issues and tracking parents in Review count toward this limit. Pause work by returning it to Backlog with its reason and a persistent handoff. Parent Issues summarize staged work; only their executable children are claimed for implementation. Board fields, blocking, cancellation, and synchronization are defined in [Project Board](https://github.com/ZenWang00/semantic-shared-control/wiki/Project-Board).

Before editing, read the relevant design and inspect the implementation. Distinguish proposed behavior from behavior that exists. Do not infer that an interface or safeguard has been implemented merely because its specification is detailed.

## Branches, integration, and review

Use a task branch and PR for substantive repository changes. Use a separate checkout or worktree when concurrent agents would otherwise interfere. Parallel agents must agree on edit boundaries and dependencies; shared contracts come from reviewed repository documentation, not private conversations.

PRs target `main` and use squash merge by default. A PR must describe the concrete behavior or artifact change, its reason, verification evidence, limitations, and the related Issue. Prefer one coherent, independently reviewable change per PR.

Use `Refs #N` within the same repository and `Refs owner/repository#N` across repositories to link an Issue. Do not use automatic-closing keywords such as `Closes #N` or `Fixes #N`: merging code may precede experimental analysis, hardware checks, or other acceptance work. Close the Issue and synchronize the board explicitly only after the complete acceptance decision.

The executing agent performs an appropriate self-check. An independent reviewer then checks the **final diff**, relevant design and acceptance criteria, and verification evidence. Record the reviewer identity or agent/session reference, reviewed commit, findings, and disposition in the PR. Resolve substantive findings before merge. If follow-up edits change the result, review the new diff and rerun only checks affected by those edits; do not reuse approval of an obsolete version.

Review records may be posted by the coordinating agent on behalf of an independent reviewer, provided the attribution is explicit. A successful tool call, an agent's completion message, or a green check alone does not establish acceptance.

## Cross-repository delivery

Follow [Repository Ownership](https://github.com/ZenWang00/semantic-shared-control/wiki/Repository-Ownership-and-Cross-Repository-Workflow) when choosing the implementation repository. The server repository owns shared contracts, reusable algorithms and evidence tools; the ROS2 repository owns integration, runtime coordination and final motion authority. Code ownership does not change deployment location.

Use one Project and the canonical server Wiki. Place executable Issues in the repository that owns their outcome, retain cross-repository parent/blocker relations, and avoid duplicate cards. Link all participating PRs. A coordinated delivery records both source revisions, the shared package/contract versions, configuration and integration evidence. A merged producer PR does not establish consumer compatibility or complete aggregate acceptance. Repository-specific setup documentation stays with its code.

## Verification and research evidence

Choose verification that can detect incorrect behavior. Exercise the important normal and failure cases identified by the Issue and affected design. Run relevant existing checks; add tests when they meaningfully protect behavior. Do not create tests that merely mirror implementation or require code tests for spelling, formatting, and other low-impact documentation changes.

Record commands or procedures, the tested revision, relevant configuration, results, and limitations. State clearly whether evidence comes from static inspection, simulation, recorded-data replay, or a real robot. These forms of evidence are not interchangeable. If a required environment is unavailable, record the missing check and keep the task open or blocked unless its scope is explicitly revised and approved under the normal decision rules.

Research tasks must preserve enough information to reproduce their conclusions: code and data references, configurations, experiment conditions, comparison method, outputs, and analysis. Label unplanned analyses as exploratory. A credible negative result or unsupported hypothesis can satisfy a research task; improving a metric is not a universal completion requirement.

Keep implementation completion separate from research validation. Do not present a runnable prototype as evidence of benefit, and do not present simulation as hardware validation. The owner confirms claims intended for the thesis or external use.

## Context continuity and handoff

An execution Issue should fit one working conversation with room for relevant inputs, code, tool results, implementation, debugging, self-checks, and delivery. The separate review conversation should be able to understand the result from persistent artifacts. Apply the decomposition rules in [Issue Standards](https://github.com/ZenWang00/semantic-shared-control/wiki/Issue-Standards) before starting work that exceeds this budget.

Before context becomes scarce, an interruption, or an executor change, leave a persistent handoff in the Issue or PR containing:

- Current branch, commit, PR, and any uncommitted work or artifact locations.
- Completed work and the acceptance criteria already supported by evidence.
- Checks performed, exact results, and checks still required.
- Open problems, blockers, decisions, and approval references.
- The next concrete step and the minimum files, inputs, and references needed to resume.

If the remaining scope is too large, preserve the work, record a scope change, and create linked follow-up execution Issues before continuing. Do not reduce acceptance criteria silently or mark the original Issue Done merely because the conversation is ending. Close a revised Issue only when its explicitly documented deliverable is accepted and all remaining work has a clear destination.

For Wiki publication, merge reviewed source changes first, then publish the corresponding files to the Wiki repository. Record the source repository commit in the publication commit. Report any failed publication or state synchronization accurately; completion must describe the state that actually exists.
