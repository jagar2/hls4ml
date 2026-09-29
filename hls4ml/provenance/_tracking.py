"""Opt-in workflow scopes and the shared hls4ml instrumentation boundary."""

from __future__ import annotations

import contextvars
import functools
import hashlib
import importlib.metadata
import inspect
import marshal
import os
import platform
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from ._identity import digest, file_reference, repository_identity, summarize
from ._journal import Journal

_CURRENT = contextvars.ContextVar('hls4ml_provenance', default=None)
_PARENT = contextvars.ContextVar('hls4ml_provenance_parent', default=None)
_CI_KEYS = (
    'GITHUB_RUN_ID',
    'GITHUB_RUN_ATTEMPT',
    'GITHUB_JOB',
    'GITHUB_WORKFLOW',
    'GITHUB_SHA',
    'CI_PIPELINE_ID',
    'CI_JOB_ID',
    'CI_COMMIT_SHA',
    'BUILD_NUMBER',
    'BUILD_URL',
)


def link(target, relationship='derived_from'):
    return {'target': target, 'type': relationship}


class _Completion(dict):
    finish_id = None


def _identities(value):
    if isinstance(value, dict):
        if 'weights' in value or 'state' in value or 'sha256' in value:
            yield digest(value)
        else:
            for item in value.values():
                yield from _identities(item)
    elif isinstance(value, list):
        for item in value:
            yield from _identities(item)


class Run:
    """Record a workflow locally, then synchronize it explicitly with ``sync``.

    Use one journal per process/run. Contexts propagate through asyncio tasks;
    thread workers require ``contextvars.copy_context()`` or their own run.
    """

    def __init__(
        self,
        name,
        *,
        repository='.',
        journal=None,
        allow_dirty=False,
        parameters=None,
        tool_versions=None,
        enabled=True,
        preserve=False,
    ):
        self.enabled = enabled
        self.preserve = preserve
        self.name = name
        self.repository = Path(repository).absolute()
        self.path = journal
        self.allow_dirty = allow_dirty
        self.parameters = parameters or {}
        self.tool_versions = tool_versions or {}
        self.id = None
        self._entered = False
        self._sources = {}
        self._producers = {}

    def __enter__(self):
        if self._entered:
            raise RuntimeError('Each Run may only be entered once')
        self._entered = True
        if not self.enabled:
            self._token = _CURRENT.set(None)
            return self
        if _CURRENT.get() is not None or self.id is not None:
            raise RuntimeError('Use run.step() for nesting; each Run may only be entered once')
        source = repository_identity(self.repository, allow_dirty=self.allow_dirty)
        import uuid

        self.path = Path(self.path or self.repository / '.dataerai' / f'{uuid.uuid4()}.jsonl').absolute()
        self.journal = Journal(self.path)
        try:
            self.code_id = self.journal.append('software_code', 'Git source revision', source)
            environment = {
                'python': platform.python_version(),
                'platform': platform.platform(),
                'packages': {dist.metadata['Name']: dist.version for dist in importlib.metadata.distributions()},
                'tools': self.tool_versions,
                'ci': {k: os.environ[k] for k in _CI_KEYS if k in os.environ},
            }
            self.id = self.journal.append(
                'simulation',
                self.name,
                {
                    'status': 'running',
                    'parameters': self.parameters,
                    'preserve_files': self.preserve,
                    'environment': environment,
                },
                [link(self.code_id, 'implements')],
            )
        except BaseException:
            self.journal.close()
            raise
        self._token = _CURRENT.set(self)
        self._parent_token = _PARENT.set(self.id)
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self.enabled:
            _CURRENT.reset(self._token)
            return
        try:
            for module in list(sys.modules.values()):
                filename = getattr(module, '__file__', None)
                if filename:
                    path = Path(filename).resolve()
                    if path.suffix == '.py' and path.is_relative_to(self.repository.resolve()) and path.is_file():
                        reference = file_reference(path)
                        if self.preserve:
                            from ._preserve import snapshot

                            snapshot(reference, self.path)
                        self.journal.append(
                            'software_code',
                            f'Imported module: {path.name}',
                            reference,
                            [{'target': self.id, 'type': 'implements', 'direction': 'incoming'}],
                        )
            self.journal.append(
                'log',
                f'{self.name}: finished',
                {
                    'status': 'succeeded'
                    if exc_type is None or isinstance(exc, SystemExit) and exc.code in (0, None)
                    else 'failed',
                    'exception_type': exc_type.__name__ if exc_type else None,
                    'source_at_end': repository_identity(self.repository, allow_dirty=True),
                },
                [link(self.id, 'records_telemetry')],
            )
        finally:
            _PARENT.reset(self._parent_token)
            _CURRENT.reset(self._token)
            self.journal.close()

    @contextmanager
    def step(self, name, *, parameters=None, inputs=(), code=None):
        if not self.enabled:
            yield _Completion()
            return
        if _CURRENT.get() is not self:
            raise RuntimeError('Steps must execute inside their active Run context')
        links = [link(_PARENT.get() or self.id, 'step_of'), link(self.code_id, 'implements')]
        links.extend(link(item) for item in inputs)
        if code is not None:
            path = str(Path(code).absolute())
            reference = file_reference(path)
            if self.preserve:
                from ._preserve import snapshot

                snapshot(reference, self.path)
            identity = digest(reference)
            if identity not in self._sources:
                self._sources[identity] = self.journal.append('software_code', Path(path).name, reference)
            links.append(link(self._sources[identity], 'implements'))
        identifier = self.journal.append('simulation', name, {'status': 'running', 'parameters': parameters or {}}, links)
        token = _PARENT.set(identifier)
        started = time.monotonic()
        completion = _Completion()
        try:
            yield completion
        except BaseException as exc:
            completion.update(
                status='succeeded' if isinstance(exc, SystemExit) and exc.code in (0, None) else 'failed',
                exception_type=type(exc).__name__,
            )
            raise
        else:
            completion.setdefault('status', 'succeeded')
        finally:
            try:
                completion['duration_seconds'] = time.monotonic() - started
                completion.finish_id = self.journal.append(
                    'log', f'{name}: finished', completion, [link(identifier, 'records_telemetry')]
                )
            finally:
                _PARENT.reset(token)

    def artifact(self, path, *, role='output', uri=None):
        if not self.enabled:
            return None
        if _CURRENT.get() is not self:
            raise RuntimeError('Artifacts must be recorded inside their active Run context')
        if role not in ('input', 'output'):
            raise ValueError('artifact role must be input or output')
        reference = file_reference(path)
        if uri is not None:
            reference['uri'] = uri
        if self.preserve:
            from ._preserve import snapshot

            snapshot(reference, self.path)
        reference['role'] = role
        parent = _PARENT.get() or self.id
        edge = {'target': parent, 'type': 'produces' if role == 'output' else 'derived_from', 'direction': 'incoming'}
        artifact = self.journal.append(
            'software_code' if reference['storage'] == 'git' else 'dataset', Path(path).name, reference, [edge]
        )
        return artifact

    def array(self, value, *, name='array', role='output'):
        """Snapshot a NumPy array without pickle; enabled only with preservation."""
        if not self.enabled or not self.preserve:
            return None
        import tempfile

        import numpy as np

        with tempfile.TemporaryDirectory(prefix='hls4ml-array-') as directory:
            path = Path(directory) / (Path(name).name + '.npy')
            np.save(path, value, allow_pickle=False)
            identifier = self.artifact(path, role=role)
        return identifier

    def artifacts(self, path, *, role='output'):
        """Record a file or directory tree, excluding this run's evidence files."""
        if not self.enabled:
            return []
        root = Path(path)
        if not root.exists():
            raise FileNotFoundError(root)
        paths = sorted(root.rglob('*')) if root.is_dir() else [root]
        excluded = {Path(str(self.path) + suffix).resolve() for suffix in ('', '.files', '.zip', '.preserved.json')}
        result = []
        for item in paths:
            resolved = item.resolve()
            if '.git' in item.parts or any(resolved == entry or entry in resolved.parents for entry in excluded):
                continue
            if item.is_file():
                result.append(self.artifact(item, role=role))
        return result

    def command(self, argv, *, cwd=None):
        cwd = Path(cwd or self.repository).resolve()
        if isinstance(argv, (str, bytes)) or not argv:
            raise ValueError('command expects a nonempty sequence of arguments')
        executable = str((cwd / argv[0]).resolve()) if '/' in str(argv[0]) else shutil.which(argv[0])
        executable = str(Path(executable).resolve()) if executable else None
        with self.step(
            'command',
            parameters={
                'argv': list(argv),
                'cwd': str(cwd),
                'executable': file_reference(executable) if executable else None,
            },
        ) as result:
            for arg in argv[1:]:
                path = cwd / arg
                if path.suffix in ('.py', '.sh', '.bash', '.tcl', '.mk') and path.is_file():
                    self.artifact(path, role='input')
            process = subprocess.run(argv, cwd=cwd, check=False)
            result['returncode'] = process.returncode
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, argv)
        return process


def _record_files(run, value):
    if not run.preserve or not isinstance(value, dict):
        return
    file_keys = {
        'config',
        'config_file',
        'file_path',
        'input_data_tb',
        'output_data_tb',
        'KerasH5',
        'KerasJson',
        'KerasModel',
        'OnnxModel',
        'PytorchModel',
        'InputData',
        'OutputPredictions',
    }
    for key, item in value.items():
        if key in file_keys and isinstance(item, (str, Path)) and Path(item).is_file():
            run.artifact(item, role='input')
        elif isinstance(item, dict):
            _record_files(run, item)


def _record_arrays(run, value, *, role, seen=None):
    if not run.preserve:
        return
    import numpy as np

    seen = set() if seen is None else seen
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, np.ndarray):
        run.array(value, role=role)
    elif isinstance(value, dict):
        for item in value.values():
            _record_arrays(run, item, role=role, seen=seen)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _record_arrays(run, item, role=role, seen=seen)


def tracked(name, *, outputs=False):
    """Preserve public signatures/return values and incur no IO when disabled."""

    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            run = _CURRENT.get()
            if run is None:
                return function(*args, **kwargs)
            bound = inspect.signature(function).bind(*args, **kwargs)
            bound.apply_defaults()
            parameters = {key: summarize(value) for key, value in bound.arguments.items() if key != 'cls'}
            parameters['executed_code_sha256'] = hashlib.sha256(marshal.dumps(function.__code__)).hexdigest()
            inputs = sorted({run._producers[key] for key in _identities(parameters) if key in run._producers})
            code = inspect.getsourcefile(function)
            if code and not Path(code).is_file():
                code = None
            with run.step(name, parameters=parameters, inputs=inputs, code=code) as completion:
                _record_arrays(run, bound.arguments, role='input')
                if function.__name__ != 'save':
                    _record_files(run, bound.arguments)
                try:
                    if args and type(args[0]).__module__ == 'hls4ml.utils.link':
                        run.artifacts(args[0].config.get_output_dir(), role='input')
                    result = function(*args, **kwargs)
                    completion['result'] = summarize(result)
                    _record_arrays(run, result, role='output')
                    _record_files(run, result)
                    if args and hasattr(args[0], 'config'):
                        completion['model_after'] = summarize(args[0])
                finally:
                    if outputs:
                        directory = Path(args[0].config.get_output_dir())
                        if directory.is_dir():
                            run.artifacts(directory)
                        archive = Path(str(directory) + '.tar.gz')
                        if archive.is_file():
                            run.artifact(archive)
                        if function.__name__ == 'save' and Path(bound.arguments['file_path']).is_file():
                            run.artifact(bound.arguments['file_path'])
            for key in _identities({'result': completion.get('result'), 'model': completion.get('model_after')}):
                run._producers[key] = completion.finish_id
            return result

        return wrapped

    return decorate
