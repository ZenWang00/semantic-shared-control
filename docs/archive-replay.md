# Candidate run archives and replay

The new format `ssc-run-v2` stores schema-2 inputs, candidates, exact configuration resources, selection/history lineage, fixed objective diagnostics, and separate proposal/acceptance/controller evidence. It does not contain executable source or program validation receipts. Old archives are explicitly unsupported by the new reader and remain reproducible through their historical Git/package versions.

```sh
python -I -m ssc_core.replay run.json --inspect
python -I -m ssc_core.replay run.json
```

Replay resolves immutable dependencies and checks the checksum, recomputes candidate scores/selection and the fixed distance residual/penalty at recorded candidate poses. It neither starts robot control nor converts archived acceptance into present authority. Missing data, configuration mismatch, tampering, dangling selection lineage and old schema/format fail explicitly. Reproducibility uses the profile tolerances, not bitwise-identical solver trajectories across environments.

Preserve source revisions, wheels/checksums and dependency versions. Keep original source timestamps and distinguish proposed targets from independently sampled synthetic feedback. Old retained P1 evidence is not relabeled as candidate-profile acceptance.
