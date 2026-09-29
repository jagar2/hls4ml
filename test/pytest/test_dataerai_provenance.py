"""Contract checks for opt-in recording without FPGA tools or a Dataerai account."""

import json
import os
import subprocess
import sys

import pytest


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()

    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()

    git('init', '-q')
    git('config', 'user.name', 'Test')
    git('config', 'user.email', 'test@example.invalid')
    git('remote', 'add', 'origin', 'https://github.com/example/workflow.git')
    (root / 'workflow.py').write_text('print("workflow")\n')
    git('add', '.')
    git('commit', '-qm', 'Initial workflow')
    return root


def test_python_runner_preserves_exit_and_records_failure(repository, tmp_path):
    journal = tmp_path / 'run.jsonl'
    result = subprocess.run(
        [
            sys.executable,
            '-m',
            'hls4ml.provenance',
            'run',
            '--repo',
            str(repository),
            '--journal',
            str(journal),
            '--',
            sys.executable,
            '-c',
            'raise SystemExit(7)',
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 7, result.stderr
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    assert events[-1]['metadata']['status'] == 'failed'
    assert any(event['metadata'].get('returncode') == 7 for event in events)


def test_git_identity_and_dirty_content_are_never_confused(repository):
    from hls4ml.provenance._identity import file_reference, repository_identity

    source = repository_identity(repository)
    ref = file_reference(repository / 'workflow.py')
    assert source['commit'] == ref['git']['commit']
    assert ref['storage'] == 'git'
    assert ref['git']['path'] == 'workflow.py'
    (repository / 'workflow.py').write_text('print("changed")\n')
    with pytest.raises(ValueError, match='dirty'):
        repository_identity(repository)
    dirty = repository_identity(repository, allow_dirty=True)
    assert dirty['dirty'] and dirty['changes'][0]['sha256']
    assert file_reference(repository / 'workflow.py')['storage'] == 'external'


def test_source_parent_history_is_referenced(repository):
    from hls4ml.provenance._identity import repository_identity

    original = repository_identity(repository)['commit']
    (repository / 'workflow.py').write_text('print("v2")\n')
    subprocess.run(['git', '-C', str(repository), 'commit', '-qam', 'Second version'], check=True)
    assert repository_identity(repository)['parents'] == [original]


def test_recording_disabled_has_no_io(monkeypatch):
    import hls4ml.provenance._tracking as tracking
    from hls4ml.provenance import tracked

    def forbidden(*args, **kwargs):
        raise AssertionError('No journal without opt-in')

    monkeypatch.setattr(tracking, 'Journal', forbidden)

    @tracked('example')
    def work(value):
        return value

    marker = object()
    assert work(marker) is marker


def test_steps_artifacts_failure_and_hash_chain(repository, tmp_path):
    from hls4ml.provenance import Run, read_journal

    path = tmp_path / 'events.jsonl'
    with pytest.raises(RuntimeError, match='operation failed'), Run('workflow', repository=repository, journal=path) as run:
        source = run.artifact(repository / 'workflow.py', role='input')
        with run.step('train', inputs=[source], parameters={'seed': 17}):
            with run.step('evaluate'):
                raise RuntimeError('operation failed')
    events = read_journal(path)
    assert events[-1]['metadata']['status'] == 'failed'
    assert len([event for event in events if event['metadata'].get('status') == 'failed']) == 3
    train = next(event for event in events if event['title'] == 'train')
    evaluate = next(event for event in events if event['title'] == 'evaluate')
    assert {'target': train['id'], 'type': 'step_of'} in evaluate['links']
    assert {'target': source, 'type': 'derived_from'} in train['links']
    assert os.stat(path).st_mode & 0o777 == 0o600
    rows = path.read_text().splitlines()
    altered = json.loads(rows[1])
    altered['metadata']['status'] = 'succeeded'
    rows[1] = json.dumps(altered)
    path.write_text('\n'.join(rows) + '\n')
    with pytest.raises(ValueError, match='hash chain'):
        read_journal(path)


def test_secrets_are_redacted_before_disk(repository, tmp_path, monkeypatch):
    from hls4ml.provenance import Run

    monkeypatch.setenv('DATAERAI_TOKEN', 'sensitive-value-123')
    journal = tmp_path / 'events.jsonl'
    with Run(
        'redaction',
        repository=repository,
        journal=journal,
        parameters={
            'password': 'another-secret',
            'argv': ['script.py', '--api-key=private-key', '--token', 'opaque'],
            'url': 'https://user:pass@example.org/repo?signature=hidden',
            'description': 'sensitive-value-123',
        },
    ):
        pass
    content = journal.read_text()
    for secret in ('sensitive-value-123', 'another-secret', 'private-key', 'opaque', 'user:pass', 'signature=hidden'):
        assert secret not in content


def test_arrays_are_hashed_not_uploaded():
    import numpy as np

    from hls4ml.provenance._identity import summarize

    array = np.array([[1, 2], [3, 4]], dtype='float32')
    summary = summarize(array)
    assert summary['shape'] == [2, 2]
    assert len(summary['sha256']) == 64
    assert 'data' not in summary
    assert summarize(array.copy()) == summary
    array[0, 0] = 5
    assert summarize(array)['sha256'] != summary['sha256']


def test_graph_conversion_write_and_optimizers_are_tracked(repository, tmp_path):
    import copy

    from hls4ml.model.graph import ModelGraph
    from hls4ml.provenance import Run, read_journal
    from hls4ml.utils.config import create_config

    config = create_config(output_dir=str(tmp_path / 'generated'), backend='Vivado')
    config['HLSConfig'] = {'Model': {'Precision': 'fixed<16,6>', 'ReuseFactor': 1}}
    layers = [
        {'name': 'features', 'class_name': 'InputLayer', 'input_shape': [2]},
        {'name': 'relu', 'class_name': 'Activation', 'activation': 'relu'},
    ]
    baseline_config = copy.deepcopy(config)
    baseline_config['OutputDir'] = str(tmp_path / 'baseline')
    baseline = ModelGraph.from_layer_list(baseline_config, copy.deepcopy(layers))
    baseline.write()
    journal = tmp_path / 'graph.jsonl'
    with Run('graph', repository=repository, journal=journal):
        model = ModelGraph.from_layer_list(config, layers)
        model.write()
    events = read_journal(journal)
    names = {event['title'] for event in events}
    assert 'hls4ml.model.graph.from_layer_list' in names
    assert 'hls4ml.model.graph.apply_flow' in names
    assert 'hls4ml.model.optimizer.optimizer.optimize_model' in names
    assert 'hls4ml.model.graph.write' in names
    artifacts = [event for event in events if event['metadata'].get('role') == 'output']
    assert any(event['title'] == 'myproject.cpp' for event in artifacts)
    assert all(event['metadata']['sha256'] for event in artifacts)
    assert all(event['links'][0]['type'] == 'produces' for event in artifacts)
    assert (tmp_path / 'baseline/firmware/myproject.cpp').read_bytes() == (
        tmp_path / 'generated/firmware/myproject.cpp'
    ).read_bytes()


@pytest.fixture
def recorded_run(repository, tmp_path):
    from hls4ml.provenance import Run

    journal = tmp_path / 'sync.jsonl'
    with Run('sync', repository=repository, journal=journal) as run:
        with run.step('conversion'):
            run.artifact(repository / 'workflow.py')
    return journal


def test_sync_uses_metadata_only_and_stable_ids(recorded_run, monkeypatch):
    from hls4ml.provenance._sync import Dataerai

    client = Dataerai('https://example.invalid', 'test-token', 'a656e705-21f3-47f7-88e3-6e3ee9b1a5e5')
    calls = []

    def request(method, path, payload):
        calls.append((method, path, payload))
        if path == '/api/assets/':
            return 201, {'id': payload['client_asset_id']}
        return 201, {'id': 'edge'}

    monkeypatch.setattr(client, 'request', request)
    first = client.sync(recorded_run)
    before = list(calls)
    second = client.sync(recorded_run)
    assert first == second
    assert before == calls[len(before) :]
    assert all('/content/' not in path for _, path, _ in calls)
    assets = {payload['client_asset_id'] for _, path, payload in before if path == '/api/assets/'}
    assert all(payload['to_asset_id'] in assets for _, path, payload in before if path.endswith('/relationships/'))


@pytest.mark.parametrize('status', [401, 403, 409, 500])
def test_sync_rejects_asset_errors_and_preserves_journal(recorded_run, monkeypatch, status):
    from hls4ml.provenance._sync import Dataerai, SyncError

    client = Dataerai('https://example.invalid', 'test-token', 'a656e705-21f3-47f7-88e3-6e3ee9b1a5e5')
    original = recorded_run.read_bytes()
    monkeypatch.setattr(client, 'request', lambda *args: (status, {}))
    with pytest.raises(SyncError, match=str(status)):
        client.sync(recorded_run)
    assert recorded_run.read_bytes() == original


def test_only_verified_duplicate_edges_are_accepted(recorded_run, monkeypatch):
    from hls4ml.provenance._sync import Dataerai, SyncError

    client = Dataerai('https://example.invalid', 'test-token', 'a656e705-21f3-47f7-88e3-6e3ee9b1a5e5')
    detail = 'An identical relationship already exists.'

    def request(method, path, payload):
        return (200, {'id': payload['client_asset_id']}) if path == '/api/assets/' else (409, {'detail': detail})

    monkeypatch.setattr(client, 'request', request)
    client.sync(recorded_run)
    detail = 'Project quota exceeded'
    with pytest.raises(SyncError, match='relationship'):
        client.sync(recorded_run)


def test_existing_journal_cannot_be_overwritten(recorded_run, repository):
    from hls4ml.provenance import Run

    original = recorded_run.read_bytes()
    with pytest.raises(FileExistsError), Run('again', repository=repository, journal=recorded_run):
        pass
    assert recorded_run.read_bytes() == original


def test_python_script_runs_instrumented_in_process(repository, tmp_path):
    script = repository / 'workflow.py'
    script.write_text('from hls4ml.provenance import tracked\n@tracked("custom")\ndef f(): return 42\nf()\n')
    subprocess.run(['git', '-C', str(repository), 'commit', '-qam', 'Instrumented script'], check=True)
    journal = tmp_path / 'script.jsonl'
    result = subprocess.run(
        [
            sys.executable,
            '-m',
            'hls4ml.provenance',
            'run',
            '--repo',
            str(repository),
            '--journal',
            str(journal),
            '--script',
            str(script),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    assert any(event['title'] == 'custom: finished' and event['metadata']['result'] == 42 for event in events)


@pytest.mark.parametrize(
    'server', ['http://example.org', 'https://user:password@example.org', 'https://example.org/?secret=x']
)
def test_sync_rejects_unsafe_origins(server):
    from hls4ml.provenance._sync import Dataerai

    with pytest.raises(ValueError):
        Dataerai(server, 'token', 'a656e705-21f3-47f7-88e3-6e3ee9b1a5e5')


def test_array_producer_is_linked_to_consumer(repository, tmp_path):
    import numpy as np

    from hls4ml.provenance import Run, read_journal, tracked

    @tracked('produce')
    def produce():
        return np.arange(3)

    @tracked('consume')
    def consume(array):
        return int(array.sum())

    journal = tmp_path / 'dataflow.jsonl'
    with Run('dataflow', repository=repository, journal=journal):
        assert consume(produce()) == 3
    events = read_journal(journal)
    produced = next(event for event in events if event['title'] == 'produce: finished')
    consumed = next(event for event in events if event['title'] == 'consume')
    assert {'target': produced['id'], 'type': 'derived_from'} in consumed['links']


def test_incomplete_journal_requires_explicit_sync(recorded_run, monkeypatch):
    from hls4ml.provenance._sync import Dataerai

    client = Dataerai('https://example.invalid', 'test-token', 'a656e705-21f3-47f7-88e3-6e3ee9b1a5e5')
    recorded_run.write_text('\n'.join(recorded_run.read_text().splitlines()[:-1]) + '\n')

    def request(method, path, payload):
        return (201, {'id': payload['client_asset_id']}) if path == '/api/assets/' else (201, {})

    monkeypatch.setattr(client, 'request', request)
    with pytest.raises(ValueError, match='incomplete'):
        client.sync(recorded_run)
    assert client.sync(recorded_run, allow_incomplete=True)['assets'] > 0


def test_http_transport_retries_without_uploads(recorded_run):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from hls4ml.provenance._sync import Dataerai, SyncError

    assets, edges, failure = {}, set(), [True]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.headers['Authorization'] == 'Bearer local-fixture'
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path == '/api/assets/':
                identifier = payload['client_asset_id']
                status = 200 if identifier in assets else 201
                assets[identifier] = payload
                response = {'id': identifier}
            else:
                assert self.path.endswith('/relationships/')
                if failure[0]:
                    failure[0] = False
                    status, response = 503, {}
                else:
                    key = (self.path, payload['to_asset_id'], payload['type'])
                    status = 409 if key in edges else 201
                    edges.add(key)
                    response = {'detail': 'An identical relationship already exists.'}
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        client = Dataerai(f'http://127.0.0.1:{server.server_port}', 'local-fixture', 'a656e705-21f3-47f7-88e3-6e3ee9b1a5e5')
        with pytest.raises(SyncError, match='503'):
            client.sync(recorded_run)
        result = client.sync(recorded_run)
        assert len(assets) == result['assets']
        counts = len(assets), len(edges)
        assert client.sync(recorded_run) == result
        assert (len(assets), len(edges)) == counts
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_github_capture_records_attempt_jobs_steps_and_artifacts(tmp_path, monkeypatch):
    import io

    import hls4ml.provenance._github as github
    from hls4ml.provenance import read_journal

    run = {
        'id': 91,
        'name': 'build',
        'status': 'completed',
        'conclusion': 'failure',
        'run_attempt': 2,
        'head_sha': 'a' * 40,
        'path': '.github/workflows/build.yml',
        'head_branch': 'main',
        'event': 'push',
        'actor': {'login': 'test'},
        'html_url': 'https://github.com/test/repo/actions/runs/91',
        'updated_at': '2026-01-01T00:00:01Z',
    }
    paths = []

    class Opener:
        def open(self, request, timeout):
            path = request.full_url.split('/repos/test/repo/')[1]
            paths.append(path)
            if path == 'actions/runs/91':
                data = run
            elif path.startswith('commits/'):
                data = {'parents': [{'sha': 'b' * 40}], 'html_url': 'https://github.com/test/repo/commit/' + 'a' * 40}
            elif '/jobs?' in path:
                data = {
                    'jobs': [
                        {
                            'id': 10,
                            'name': 'synthesis',
                            'conclusion': 'failure',
                            'steps': [{'name': 'compile', 'number': 1, 'status': 'completed', 'conclusion': 'failure'}],
                        }
                    ]
                }
            else:
                data = {'artifacts': [{'name': 'report', 'id': 42, 'digest': 'sha256:' + 'c' * 64, 'expired': False}]}
            return io.BytesIO(json.dumps(data).encode())

    monkeypatch.setattr(github, 'build_opener', lambda *args: Opener())
    journal = tmp_path / 'github.jsonl'
    github.capture_github_run('test/repo', 91, 'fixture', journal)
    events = read_journal(journal)
    assert events[0]['metadata']['commit'] == 'a' * 40
    assert events[1]['metadata']['attempt'] == 2
    assert events[-1]['metadata']['conclusion'] == 'failure'
    assert any('/attempts/2/jobs?' in path for path in paths)
    assert any(event['metadata'].get('digest') == 'sha256:' + 'c' * 64 for event in events)
    assert all('/logs' not in path for path in paths)


def test_failed_build_records_partial_artifacts_without_raw_logs(repository, tmp_path, monkeypatch):
    from hls4ml.model.graph import HLSConfig, ModelGraph
    from hls4ml.provenance import Run, read_journal
    from hls4ml.utils.config import create_config

    output = tmp_path / 'failed-build'
    output.mkdir()
    config = create_config(output_dir=str(output), backend='Vivado')
    config['HLSConfig'] = {'Model': {'Precision': 'fixed<16,6>', 'ReuseFactor': 1}}
    model = ModelGraph(HLSConfig(config), inputs=[], outputs=[])

    def fail_build(model, **kwargs):
        (output / 'build.log').write_text('private raw log content')
        raise RuntimeError('tool failed')

    monkeypatch.setattr(model.config.backend, 'build', fail_build)
    journal = tmp_path / 'failed-build.jsonl'
    with pytest.raises(RuntimeError, match='tool failed'), Run('build', repository=repository, journal=journal):
        model.build(synth=True)
    events = read_journal(journal)
    log = next(event for event in events if event['title'] == 'build.log')
    assert len(log['metadata']['sha256']) == 64
    assert events[-1]['metadata']['status'] == 'failed'
    assert 'private raw log content' not in journal.read_text()


def test_http_redirect_does_not_forward_bearer(recorded_run):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from hls4ml.provenance._sync import Dataerai, SyncError

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(self.path)
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(307)
            self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/redirected')
            self.end_headers()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        client = Dataerai(f'http://127.0.0.1:{server.server_port}', 'fixture', 'a656e705-21f3-47f7-88e3-6e3ee9b1a5e5')
        with pytest.raises(SyncError, match='307'):
            client.sync(recorded_run)
        assert requests == ['/api/assets/']
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
