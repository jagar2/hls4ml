"""Exercise the published tutorial scripts as a reader would run them."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from hls4ml.provenance import read_journal

TUTORIALS = Path(__file__).resolve().parents[2] / 'docs/tutorials/dataerai'


@pytest.fixture
def tutorial_repo(tmp_path):
    repo = tmp_path / 'tutorial'
    repo.mkdir()
    for filename in ('first_workflow.py', 'inspect_journal.py'):
        shutil.copyfile(TUTORIALS / filename, repo / filename)
    (repo / '.gitignore').write_text('.dataerai/\n')
    for command in (
        ['init', '-q'],
        ['config', 'user.name', 'Tutorial Test'],
        ['config', 'user.email', 'tutorial@example.invalid'],
        ['remote', 'add', 'origin', 'https://github.com/example/tutorial.git'],
        ['add', '.'],
        ['commit', '-qm', 'Tutorial sources'],
    ):
        subprocess.run(['git', '-C', str(repo), *command], check=True, capture_output=True)
    return repo


def run_script(repo, name, *args, expected=0):
    process = subprocess.run([sys.executable, str(repo / name), *args], cwd=repo, capture_output=True, text=True)
    assert process.returncode == expected, process.stdout + process.stderr
    return process


def test_complete_tutorial_records_fit_graph_and_references(tutorial_repo):
    run_script(tutorial_repo, 'first_workflow.py')
    output = tutorial_repo / '.dataerai/tutorial'
    metrics = json.loads((output / 'metrics.json').read_text())
    assert metrics['numpy_training_mse'] < 1e-20
    assert metrics['hls_inference_measured'] is False
    with np.load(output / 'model.npz') as model:
        np.testing.assert_allclose(model['weight'][:, 0], [0.75, -0.25])
        np.testing.assert_allclose(model['bias'], [0.1])
    assert (output / 'hls/firmware/linear_tutorial.cpp').is_file()
    inspection = run_script(
        tutorial_repo, 'inspect_journal.py', str(output / 'run.jsonl'), '--dot', str(output / 'graph.dot')
    )
    report = json.loads(inspection.stdout)
    assert report['status'] == 'succeeded'
    events = read_journal(output / 'run.jsonl')
    dataset = next(event for event in events if event['title'] == 'dataset.npz')
    fit = next(event for event in events if event['title'] == 'fit_linear_model')
    assert {'source': fit['id'], 'type': 'derived_from', 'target': dataset['id']} in report['edges']
    assert any(event['metadata'].get('git', {}).get('path') == 'first_workflow.py' for event in events)
    assert (output / 'graph.dot').read_text().startswith('digraph provenance {')
    assert not subprocess.check_output(['git', '-C', str(tutorial_repo), 'status', '--porcelain'])


def test_failure_tutorial_preserves_completed_outputs(tutorial_repo):
    run_script(tutorial_repo, 'first_workflow.py', '--fail-after-fit', expected=1)
    output = tutorial_repo / '.dataerai/tutorial'
    report = json.loads(run_script(tutorial_repo, 'inspect_journal.py', str(output / 'run.jsonl')).stdout)
    assert report['status'] == 'failed'
    assert (output / 'model.npz').is_file()
    assert not (output / 'hls').exists()
    assert any(artifact['title'] == 'model.npz' for artifact in report['artifacts'])


def test_changed_seed_preserves_source_and_distinguishes_runs(tutorial_repo):
    for seed in (42, 43):
        run_script(tutorial_repo, 'first_workflow.py', '--seed', str(seed), '--output', f'.dataerai/seed{seed}')
    runs = [read_journal(tutorial_repo / f'.dataerai/seed{seed}/run.jsonl') for seed in (42, 43)]
    assert runs[0][0]['metadata']['commit'] == runs[1][0]['metadata']['commit']
    assert runs[0][1]['id'] != runs[1][1]['id']
    assert [events[1]['metadata']['parameters']['seed'] for events in runs] == [42, 43]
    datasets = [next(event['metadata'] for event in events if event['title'] == 'dataset.npz') for events in runs]
    assert datasets[0]['sha256'] != datasets[1]['sha256']
    with np.load(tutorial_repo / '.dataerai/seed42/dataset.npz') as first:
        with np.load(tutorial_repo / '.dataerai/seed43/dataset.npz') as second:
            assert not np.array_equal(first['x'], second['x'])


def test_inspector_rejects_tampered_tutorial_journal(tutorial_repo):
    run_script(tutorial_repo, 'first_workflow.py', '--fail-after-fit', expected=1)
    journal = tutorial_repo / '.dataerai/tutorial/run.jsonl'
    rows = journal.read_text().splitlines()
    final = json.loads(rows[-1])
    final['metadata']['status'] = 'succeeded'
    rows[-1] = json.dumps(final)
    journal.write_text('\n'.join(rows) + '\n')
    result = run_script(tutorial_repo, 'inspect_journal.py', str(journal), expected=1)
    assert 'hash chain is invalid' in result.stderr
