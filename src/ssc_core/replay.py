"""Data-only replay: deterministic selection and fixed distance diagnostics.

No optimizer, worker, archived code, factory, or controller is instantiated.
"""
import argparse
from dataclasses import asdict
import json
import math
import sys

from .archive import RunArchive, environment, inspect_archive
from .fusion import estimate_intent, get_intent_config
from .optimization import get_solver_config
from .policy import NUMERIC_CANDIDATES_V2
from .validation import to_dict

def replay_archive(archive: RunArchive):
    registry, manifest = archive.registry(), archive.manifest
    current = environment()
    report = {'archive_sha256': archive.sha256, 'run_id': manifest['run_id'],
              'evidence_scope': 'offline candidate selection and distance replay; no command execution',
              'recorded_environment': manifest['environment'], 'replay_environment': current,
              'environment_differences': {key: {'recorded': manifest['environment'][key], 'replay': value}
                  for key, value in current.items() if value != manifest['environment'][key]},
              'tolerances': manifest['replay_tolerances'], 'selections': [], 'evaluations': [], 'ok': True}
    def close(a, b):
        return math.isclose(a, b, abs_tol=manifest['replay_tolerances']['atol'],
                            rel_tol=manifest['replay_tolerances']['rtol'])
    payload = archive.to_dict()
    selections = {item['data']['selection_id']: item['data'] for item in payload['records']
                  if item['record_type'] == 'CandidateSelection'}
    selections.update({item['payload']['selection_id']: item['payload'] for item in payload['messages']
                       if item['message_type'] == 'CandidateSelection'})
    for identifier in sorted(selections):
        expected = registry.get_record('CandidateSelection', identifier, session_id=registry.session_id)
        resolved = registry.resolve_snapshot(expected.snapshot_id)
        outcome = estimate_intent(resolved, get_intent_config(expected.estimator_config_id),
                                  selection_id=identifier, policy=NUMERIC_CANDIDATES_V2)
        actual = outcome.selection
        ok = actual is not None and actual.selected_candidate_id == expected.selected_candidate_id
        if actual is not None:
            ok = ok and [s.candidate_id for s in actual.scores] == [s.candidate_id for s in expected.scores]
            ok = ok and all(close(a.score, b.score) and close(a.distance_m, b.distance_m)
                            for a, b in zip(actual.scores, expected.scores))
            ok = ok and close(actual.assistance_strength, expected.assistance_strength)
        report['selections'].append({'selection_id': identifier, 'snapshot_id': expected.snapshot_id,
            'expected': to_dict(expected), 'actual': to_dict(actual) if actual else None,
            'ok': bool(ok), 'diagnostic': outcome.diagnostic})
        report['ok'] = report['ok'] and bool(ok)
    config = get_solver_config(manifest['solver_config_id'])
    for event in archive.events:
        if event['kind'] != 'evaluation':
            continue
        data = event['data']
        result = registry.get_record('ControlResult', data['result_id'], session_id=registry.session_id)
        resolved = registry.resolve_snapshot(result.snapshot_id)
        selection = registry.get_record('CandidateSelection', result.selection_id, session_id=registry.session_id)
        candidate = next(point for point in resolved.bundle.candidates
                         if point.candidate_id == selection.selected_candidate_id)
        distance = math.dist(data['pose']['position_xyz'], candidate.position_xyz)
        raw = distance - config.distance_tolerance_m
        actual = {'candidate_id': candidate.candidate_id, 'distance_m': distance, 'raw_g': raw,
                  'normalization_scale': config.distance_scale_m,
                  'penalty': (max(0., raw) / config.distance_scale_m) ** 2}
        expected = asdict(result.candidate_evaluation)
        ok = actual['candidate_id'] == expected['candidate_id'] and all(
            close(actual[key], expected[key]) for key in actual if key != 'candidate_id')
        report['evaluations'].append({'event_id': event['event_id'], 'snapshot_id': result.snapshot_id,
            'result_id': result.result_id, 'candidate_id': candidate.candidate_id,
            'inputs': {'position_xyz': data['pose']['position_xyz'], 'candidate_position_xyz': list(candidate.position_xyz)},
            'expected': expected, 'actual': actual, 'ok': ok})
        report['ok'] = report['ok'] and ok
    report['selection_count'] = len(report['selections'])
    report['evaluation_count'] = len(report['evaluations'])
    report['numerical_evidence'] = 'compared' if report['evaluations'] else 'unavailable (no recorded evaluations)'
    inspection = inspect_archive(archive)
    for key in ('proposed_results', 'acceptance_decisions', 'controller_requests', 'controller_observations', 'events'):
        report[key] = inspection[key]
    return report

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive')
    parser.add_argument('--sha256', help='externally retained archive file SHA-256')
    parser.add_argument('--inspect', action='store_true', help='validate and inspect without numerical execution')
    args = parser.parse_args(argv)
    try:
        archive = RunArchive.read(args.archive, expected_sha256=args.sha256)
        report = inspect_archive(archive) if args.inspect else replay_archive(archive)
        print(json.dumps(report, sort_keys=True, allow_nan=False))
        return 0 if report.get('ok', True) else 1
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(json.dumps({'ok': False, 'code': getattr(exc, 'code', 'invalid_archive'), 'diagnostic': str(exc)}), file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
