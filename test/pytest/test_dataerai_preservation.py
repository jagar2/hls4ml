"""Preservation contracts: immutable bytes, opt-in effects, and recovery."""

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from hls4ml.provenance import Run, read_journal


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    (root / 'source.py').write_text('print(1)\n')
    for args in (
        ['init', '-q'],
        ['config', 'user.name', 'Test'],
        ['config', 'user.email', 'test@example.invalid'],
        ['remote', 'add', 'origin', 'https://github.com/example/test.git'],
        ['add', '.'],
        ['commit', '-qm', 'Source'],
    ):
        subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
    return root


def test_disabled_run_does_not_require_git_or_write(tmp_path):
    with Run('disabled', repository=tmp_path, enabled=False, preserve=True) as run:
        with run.step('work') as result:
            result['answer'] = 42
            assert run.artifact(tmp_path / 'absent') is None
    assert list(tmp_path.iterdir()) == []


def test_snapshots_keep_every_version_and_git_is_reference(repository, tmp_path):
    journal = tmp_path / 'run.jsonl'
    data = tmp_path / 'data.bin'
    with Run('versions', repository=repository, journal=journal, preserve=True) as run:
        data.write_bytes(b'first')
        run.artifact(data)
        data.write_bytes(b'second')
        run.artifact(data)
        run.artifact(repository / 'source.py', role='input')
    data.unlink()
    from hls4ml.provenance import preservation_bundle

    bundle = preservation_bundle(journal)
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert archive.read('run.jsonl') == journal.read_bytes()
        assert len(manifest['files']) == 2
        assert {archive.read(item['member']) for item in manifest['files']} == {b'first', b'second'}
        assert all(hashlib.sha256(archive.read(item['member'])).hexdigest() == item['sha256'] for item in manifest['files'])
        assert len(manifest['git_references']) >= 1
        assert all('source.py' not in name for name in archive.namelist())


def test_failed_run_can_be_preserved(repository, tmp_path):
    journal = tmp_path / 'failed.jsonl'
    with pytest.raises(RuntimeError, match='science failed'):
        with Run('failure', repository=repository, journal=journal, preserve=True) as run:
            data = tmp_path / 'checkpoint'
            data.write_bytes(b'checkpoint')
            run.artifact(data)
            raise RuntimeError('science failed')
    from hls4ml.provenance import preservation_bundle

    assert preservation_bundle(journal).is_file()
    assert read_journal(journal)[-1]['metadata']['status'] == 'failed'


def test_bundle_rejects_tampered_snapshot(repository, tmp_path):
    journal = tmp_path / 'run.jsonl'
    with Run('tamper', repository=repository, journal=journal, preserve=True) as run:
        data = tmp_path / 'data'
        data.write_bytes(b'original')
        run.artifact(data)
    event = next(e for e in read_journal(journal) if e['title'] == 'data')
    (Path(str(journal) + '.files') / event['metadata']['sha256']).write_bytes(b'changed')
    from hls4ml.provenance import preservation_bundle

    with pytest.raises(ValueError, match='checksum'):
        preservation_bundle(journal)


def test_metadata_only_bundle_rejects_changed_file(repository, tmp_path):
    journal = tmp_path / 'run.jsonl'
    data = tmp_path / 'data'
    data.write_bytes(b'before')
    with Run('old', repository=repository, journal=journal) as run:
        run.artifact(data)
    data.write_bytes(b'after')
    from hls4ml.provenance import preservation_bundle

    with pytest.raises(ValueError, match='checksum'):
        preservation_bundle(journal)


def test_upload_requires_completed_content_and_correct_server(repository, tmp_path):
    from hls4ml.provenance import preserve_to_dataerai

    journal = tmp_path / 'run.jsonl'
    with Run('upload', repository=repository, journal=journal, preserve=True):
        pass
    calls = []

    class Client:
        def auth_status(self):
            return SimpleNamespace(user_email='test@example.invalid', server_url='https://beta.dataerai.com')

        def upload(self, path, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(asset_id='asset', content_id='content', total_bytes=Path(path).stat().st_size)

    result = preserve_to_dataerai(journal, client=Client(), server='https://beta.dataerai.com', project='project')
    assert result['content_id'] == 'content'
    assert calls[0]['owner_id'] == 'project'
    with pytest.raises(ValueError, match='server'):
        preserve_to_dataerai(journal, client=Client(), server='https://other.invalid', project='project')
    assert len(calls) == 1


def test_tutorial_disabled_and_preserved_modes(repository, tmp_path):
    import sys

    script = Path(__file__).resolve().parents[2] / 'docs/tutorials/dataerai/first_workflow.py'
    for switch in ('--no-dataerai', '--dataerai-preserve'):
        output = tmp_path / switch.removeprefix('--')
        result = subprocess.run(
            [sys.executable, str(script), '--repo', str(repository), '--output', str(output), switch],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert (output / 'model.npz').is_file()
        assert (output / 'hls/firmware/linear_tutorial.cpp').is_file()
        assert (output / 'run.jsonl').exists() == (switch == '--dataerai-preserve')
        assert (output / 'run.jsonl.zip').exists() == (switch == '--dataerai-preserve')


@pytest.mark.parametrize('command', ['config', 'convert', 'build', 'report'])
def test_every_cli_command_accepts_provenance_switches(command):
    import sys

    result = subprocess.run(
        [sys.executable, '-c', 'from hls4ml.cli import main; main()', command, '--help'], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    for option in ('--dataerai', '--no-dataerai', '--dataerai-preserve', '--dataerai-sync', '--dataerai-upload'):
        assert option in result.stdout


def test_tracked_numpy_inputs_and_outputs_survive_mutation(repository, tmp_path):
    import io

    import numpy as np

    from hls4ml.provenance import preservation_bundle, tracked

    journal = tmp_path / 'arrays.jsonl'

    @tracked('double')
    def double(array):
        return array * 2

    with Run('arrays', repository=repository, journal=journal, preserve=True):
        source = np.array([1.0, 2.0])
        output = double(source)
        source[:] = 99
        output[:] = 0
    with zipfile.ZipFile(preservation_bundle(journal)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        arrays = [
            np.load(io.BytesIO(archive.read(ref['member'])), allow_pickle=False)
            for ref in manifest['files']
            if ref['path'].endswith('.npy')
        ]
        assert len(arrays) == 2
        np.testing.assert_array_equal(arrays[0], [1.0, 2.0])
        np.testing.assert_array_equal(arrays[1], [2.0, 4.0])


def test_preservation_bundle_is_repeatable(repository, tmp_path):
    from hls4ml.provenance import preservation_bundle

    journal = tmp_path / 'run.jsonl'
    with Run('repeat', repository=repository, journal=journal, preserve=True):
        pass
    first = preservation_bundle(journal).read_bytes()
    assert preservation_bundle(journal).read_bytes() == first


def test_shared_switches_preserve_outer_scope_and_explicit_disable(repository, tmp_path):
    import argparse

    from hls4ml.provenance import add_arguments, tracked, workflow

    parser = argparse.ArgumentParser()
    add_arguments(parser)

    @tracked('called')
    def called():
        return 42

    journal = tmp_path / 'outer.jsonl'
    with Run('outer', repository=repository, journal=journal):
        with workflow(parser.parse_args([]), 'inner'):
            assert called() == 42
        with workflow(parser.parse_args(['--no-dataerai']), 'disabled'):
            assert called() == 42
    assert sum(event['title'] == 'called' for event in read_journal(journal)) == 1
    with pytest.raises(ValueError, match='cannot be combined'):
        with workflow(parser.parse_args(['--no-dataerai', '--dataerai-upload']), 'conflict'):
            pass


def test_upload_incomplete_confirmation_is_not_success(repository, tmp_path):
    from hls4ml.provenance import preserve_to_dataerai

    journal = tmp_path / 'run.jsonl'
    with Run('upload', repository=repository, journal=journal):
        pass

    class Client:
        def auth_status(self):
            return SimpleNamespace(user_email='test@example.invalid', server_url='https://beta.dataerai.com')

        def upload(self, path, **kwargs):
            return SimpleNamespace(asset_id='asset', content_id='', total_bytes=0)

    with pytest.raises(RuntimeError, match='complete bundle'):
        preserve_to_dataerai(journal, client=Client(), server='https://beta.dataerai.com', project='project')
    assert not Path(str(journal) + '.preserved.json').exists()


def test_tree_capture_excludes_its_own_snapshots(repository, tmp_path):
    from hls4ml.provenance import preservation_bundle

    output = tmp_path / 'outputs'
    output.mkdir()
    journal = output / 'run.jsonl'
    with Run('tree', repository=repository, journal=journal, preserve=True) as run:
        (output / 'data').write_bytes(b'payload')
        run.artifacts(output)
        run.artifacts(output)
    with zipfile.ZipFile(preservation_bundle(journal)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert len(manifest['files']) == 2
        assert all(item['path'].endswith('/data') for item in manifest['files'])
        assert len([name for name in archive.namelist() if name.startswith('files/')]) == 1


def test_preservation_rejects_symlinks_and_object_arrays(repository, tmp_path):
    import numpy as np

    journal = tmp_path / 'run.jsonl'
    with Run('unsafe types', repository=repository, journal=journal, preserve=True) as run:
        link = tmp_path / 'symlink'
        link.symlink_to(repository / 'source.py')
        with pytest.raises(ValueError, match='symlink'):
            run.artifact(link)
        with pytest.raises(ValueError, match='Object arrays'):
            run.array(np.array([object()], dtype=object))


def test_generated_archive_is_preserved(repository, tmp_path):
    from hls4ml.provenance import tracked

    output = tmp_path / 'hls'

    class Graph:
        config = SimpleNamespace(get_output_dir=lambda: str(output))

        @tracked('write', outputs=True)
        def write(self):
            output.mkdir()
            (output / 'design.cpp').write_bytes(b'cpp')
            Path(str(output) + '.tar.gz').write_bytes(b'archive')

    journal = tmp_path / 'archive.jsonl'
    with Run('archive', repository=repository, journal=journal, preserve=True):
        Graph().write()
    assert any(e['title'] == 'hls.tar.gz' for e in read_journal(journal))


def test_configuration_dependencies_are_preserved(repository, tmp_path):
    from hls4ml.converters import parse_yaml_config

    data = tmp_path / 'input.npy'
    data.write_bytes(b'testbench-data')
    config = tmp_path / 'config.yml'
    config.write_text(f'InputData: {data}\n')
    journal = tmp_path / 'config.jsonl'
    with Run('config', repository=repository, journal=journal, preserve=True):
        parse_yaml_config(str(config))
    artifacts = [e['metadata'] for e in read_journal(journal) if e['metadata'].get('role') == 'input']
    assert {str(data), str(config)}.issubset({e['path'] for e in artifacts})


@pytest.mark.parametrize('backend', ['vivado', 'quartus'])
def test_cli_build_failure_is_not_reported_as_success(backend, monkeypatch, tmp_path):
    import hls4ml.cli as cli

    calls = []

    def shell(command):
        calls.append(command)
        return 0 if command.startswith('command -v') else 7 << 8

    monkeypatch.setattr(cli.os, 'system', shell)
    monkeypatch.setattr(
        cli.hls4ml.converters, 'parse_yaml_config', lambda _: {'ProjectName': 'example', 'OutputDir': str(tmp_path)}
    )
    with pytest.raises(SystemExit) as error:
        getattr(cli, f'_build_{backend}')(SimpleNamespace(project='example', list_options=False), ['--synthesis'])
    assert error.value.code == 7
