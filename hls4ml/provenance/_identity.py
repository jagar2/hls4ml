"""Git and content identities; source and data bytes never enter the journal."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_SECRET = re.compile(r'token|secret|password|passwd|authorization|api[_-]?key|credential', re.I)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def clean_url(value):
    if value.startswith('git@'):
        host, path = value[4:].split(':', 1)
        value = f'https://{host}/{path}'
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https', 'ssh') or not parsed.hostname:
        return None
    host = parsed.hostname
    if parsed.port:
        host += f':{parsed.port}'
    return urlunsplit((parsed.scheme, host, parsed.path.removesuffix('.git'), '', ''))


def redact(value):
    if isinstance(value, dict):
        return {str(k): '[REDACTED]' if _SECRET.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        result, hide_next = [], False
        for item in value:
            if hide_next:
                result.append('[REDACTED]')
                hide_next = False
            elif isinstance(item, str) and item.startswith('-') and _SECRET.search(item.split('=')[0]):
                result.append(item.split('=')[0] + '=[REDACTED]' if '=' in item else item)
                hide_next = '=' not in item
            else:
                result.append(redact(item))
        return result
    if isinstance(value, str):
        for key, secret in os.environ.items():
            if _SECRET.search(key) and len(secret) >= 4:
                value = value.replace(secret, '[REDACTED]')
        if re.match(r'^(https?|ssh)://', value):
            return clean_url(value)
    return value


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL).decode().strip()


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def repository_identity(path, *, allow_dirty=False):
    root = Path(git(path, 'rev-parse', '--show-toplevel'))
    commit = git(root, 'rev-parse', 'HEAD')
    status = git(root, 'status', '--porcelain=v1', '--untracked-files=all')
    if status and not allow_dirty:
        raise ValueError('Git worktree is dirty; commit changes or explicitly set allow_dirty=True / --allow-dirty')
    try:
        remote = clean_url(git(root, 'remote', 'get-url', 'origin'))
    except subprocess.CalledProcessError:
        remote = None
    changes = []
    if status:
        # No patches or file contents: record differences without copying source.
        names = (
            subprocess.check_output(
                ['git', '-C', str(root), 'ls-files', '-z', '--modified', '--others', '--exclude-standard']
            )
            .decode()
            .split('\0')
        )
        staged = (
            subprocess.check_output(['git', '-C', str(root), 'diff', '--cached', '--name-only', '-z']).decode().split('\0')
        )
        for name in sorted(set(names + staged) - {''}):
            target = root / name
            changes.append(
                {'path': name, 'sha256': file_hash(target) if target.is_file() and not target.is_symlink() else None}
            )
    return {
        'repository': remote,
        'commit': commit,
        'tree': git(root, 'rev-parse', 'HEAD^{tree}'),
        'parents': git(root, 'show', '-s', '--format=%P', 'HEAD').split(),
        'branch': git(root, 'rev-parse', '--abbrev-ref', 'HEAD'),
        'shallow': git(root, 'rev-parse', '--is-shallow-repository') == 'true',
        'dirty': bool(status),
        'changes': changes,
        'submodules': git(root, 'submodule', 'status', '--recursive').splitlines(),
        'commit_url': f'{remote}/commit/{commit}' if remote and 'github.com/' in remote else None,
    }


def file_reference(path):
    path = Path(path).absolute()
    if path.is_symlink():
        return {'path': str(path), 'kind': 'symlink', 'target': os.readlink(path), 'storage': 'external'}
    value = {'path': str(path), 'sha256': file_hash(path), 'size_bytes': path.stat().st_size, 'storage': 'external'}
    try:
        root = Path(git(path.parent, 'rev-parse', '--show-toplevel'))
        relative = path.relative_to(root).as_posix()
        commit = git(root, 'rev-parse', 'HEAD')
        blob = git(root, 'rev-parse', f'HEAD:{relative}')
        actual = git(root, 'hash-object', '--no-filters', str(path))
        remote = clean_url(git(root, 'remote', 'get-url', 'origin'))
    except (subprocess.CalledProcessError, ValueError):
        return value
    value['git'] = {
        'repository': remote,
        'commit': commit,
        'path': relative,
        'blob': blob,
        'matches_worktree': blob == actual,
    }
    if blob == actual:
        value['storage'] = 'git'
    return value


def summarize(value):
    """Describe scientific objects without dumping tensors or calling user repr."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        import math

        return value if math.isfinite(value) else str(value)
    if isinstance(value, Path):
        return file_reference(value) if value.is_file() else str(value)
    if isinstance(value, argparse.Namespace):
        return summarize(vars(value))
    if isinstance(value, dict):
        return {str(key): summarize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [summarize(item) for item in value]
    if isinstance(value, set):
        return sorted((summarize(item) for item in value), key=lambda item: canonical(item))
    # Numpy is a core hls4ml dependency, but remains outside module import.
    import numpy as np

    if isinstance(value, np.ndarray):
        if value.dtype.hasobject:
            raise ValueError('Object arrays need an explicit external artifact reference')
        return {
            'kind': 'ndarray',
            'shape': list(value.shape),
            'dtype': str(value.dtype),
            'sha256': hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest(),
        }
    if isinstance(value, np.generic):
        return summarize(value.item())
    module = type(value).__module__
    if module == 'hls4ml.utils.link':
        return {'kind': type(value).__name__, 'config': summarize(value.config.config), 'capture': 'filesystem_project'}
    if (
        module.startswith(('keras.', 'tensorflow.', 'torch.'))
        or hasattr(value, 'state_dict')
        or callable(getattr(value, 'get_weights', None))
    ):
        if callable(getattr(value, 'get_config', None)) and callable(getattr(value, 'get_weights', None)):
            return {
                'kind': type(value).__name__,
                'config': summarize(value.get_config()),
                'weights': summarize(value.get_weights()),
            }
        if callable(getattr(value, 'state_dict', None)):
            return {
                'kind': type(value).__name__,
                'state': {key: summarize(tensor.detach().cpu().numpy()) for key, tensor in value.state_dict().items()},
            }
    if module.startswith('onnx.') and callable(getattr(value, 'SerializeToString', None)):
        return {
            'kind': type(value).__name__,
            'sha256': hashlib.sha256(value.SerializeToString(deterministic=True)).hexdigest(),
        }
    if type(value).__module__ == 'hls4ml.model.graph' and hasattr(value, 'config'):
        return {
            'kind': type(value).__name__,
            'config': summarize(value.config.config),
            'layers': [layer.name for layer in value.get_layers()],
            'weights': [summarize(weight.data) for layer in value.get_layers() for weight in layer.get_weights()],
            'applied_flows': summarize(getattr(value, '_applied_flows', [])),
        }
    return {'kind': f'{type(value).__module__}.{type(value).__qualname__}', 'capture': 'type_only'}
