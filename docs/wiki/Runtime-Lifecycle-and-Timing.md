# Runtime Lifecycle and Timing

The schema-2 runtime retains local authority, independent feedback and bounded solve timing while replacing generated-program preparation with candidate-data validation.

## Preparation and activation

Validate and register a complete compatible configuration, task, observation, geometry, semantics and candidate set. Activate atomically using the current initialization attempt; a late preparation cannot restore an invalidated context. Activation and worker preparation happen while motion is disabled. Data validity never implies user engagement.

A task, scene or candidate-set replacement invalidates in-flight authority, clears previous selection and requires the existing reinitialization/engagement path. Ordinary release retains valid scene data but withdraws motion immediately and clears selection.

## Live cycle

Acquire fresh measured feedback and input, freeze the exact snapshot and previous accepted command/selection lineage, compute selection and dispatch the supervised position solver. Source timestamps are preserved. Snapshot expiry is fixed at creation; dispatch and late results do not renew it. The solve budget is bounded by original expiry and measured using a real monotonic clock.

The local gate independently validates session/epoch, ordering, identities, selection, current input enable/freshness, feedback, step/host constraints and source-orientation preservation. Only accepted commands reach the controller and contribute accepted-command smoothing history. Old/duplicate/forged results cannot cancel a newer target or refresh liveness.

## Stop and recovery

- Release/disable cancels the target locally without waiting for worker completion. It clears selector state; historical input cannot continue assistance.
- Stale input/state, timeout, invalid calculation or expired authority yields local hold/stop and the appropriate PAUSED recovery. Partial optimizer iterates are never targets.
- Invalid context requires reinitialization. Faults remain latched until explicit rearm. Ordinary release uses the existing neutral/re-engagement path.
- Solver cancellation retires the process group and reaps children. Process teardown never extends motion authority.
- At equal simulation timestamps, withdrawal precedes plant advancement/feedback, result acceptance, watchdog and new dispatch. Controller target expiry is not renewed by reading an old target.

The synthetic plant stores accepted targets, then independently advances on 20 ms ticks with bounded velocity. Feedback is sampled from committed synthetic joints, not copied from the command. Hold/cancel/stop remove target advancement. The configured values are software simulation settings, not measured physical stopping guarantees.

## Provenance

Keep proposals, decisions, applied-command references and controller observations distinct. Archive exact input/configuration/candidate identities, selection/reset provenance and deadlines. Replay checks numerical evidence only and cannot activate a context or issue commands.
