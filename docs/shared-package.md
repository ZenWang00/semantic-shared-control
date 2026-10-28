# Shared candidate-point package

`ssc-core==0.11.0` is a Python 3.12 package for schema-2 candidate data, deterministic single-target selection, fixed position optimization and immutable numerical replay. It has no ROS/GPU/model-service import dependency. NumPy 1.26.4 and SciPy 1.11.4 are used by the isolated solver; contract parsing and package import remain standard-library only.

```sh
python3.12 -m pip wheel --no-deps --wheel-dir dist .
python3.12 -m venv .venv-acceptance
.venv-acceptance/bin/python -m pip install 'dist/ssc_core-0.11.0-py3-none-any.whl[test]'
.venv-acceptance/bin/python -m pytest tests/shared_control
.venv-acceptance/bin/python -m pip check
```

Run bounded-worker and installed-wheel acceptance on Linux. Other platforms can run data and pure numerical development checks; they do not replace Linux supervision evidence.

Use immutable records through `parse_record`/`parse_message`, not unchecked direct construction at trust boundaries. Candidate proposals are data only; no generated-source public API remains. See [candidate contract](wiki/Candidate-Point-Contract.md), [profile](wiki/Offline-Candidate-Profile.md), [selection](intent-fusion.md), [optimizer](optimizer.md) and [archive/replay](archive-replay.md).

The runtime owns atomic activation, final acceptance and motion. An accepted candidate set or solver result alone grants no command authority. Schema 1.0/generated-program archives require the historical software version; the new version rejects them explicitly.
