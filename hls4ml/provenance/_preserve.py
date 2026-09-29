"""Snapshot recorded files and preserve a verifiable run bundle through the SDK."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from ._identity import canonical, file_hash
from ._journal import read_journal


def snapshot(reference, journal):
    if reference['storage'] == 'git':
        return
    if reference.get('kind') == 'symlink':
        raise ValueError('Preservation requires regular files, not symlinks')
    directory = Path(str(journal) + '.files')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / reference['sha256']
    if not target.exists():
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                with Path(reference['path']).open('rb') as source:
                    while block := source.read(1024 * 1024):
                        stream.write(block)
                stream.flush()
                stream.close()
                if file_hash(temporary) != reference['sha256']:
                    raise ValueError('File changed during preservation; checksum mismatch')
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
    if file_hash(target) != reference['sha256']:
        raise ValueError('Preserved snapshot checksum mismatch')
    reference['snapshot'] = reference['sha256']


def preservation_bundle(journal):
    """Build a ZIP with the journal, manifest, and every recorded external file.

    Git-backed files remain references. Older metadata-only journals can be
    preserved only while the original bytes still match their recorded hashes.
    """
    journal = Path(journal)
    events = read_journal(journal)
    if (
        len(events) < 2
        or events[-1]['metadata'].get('status') not in ('succeeded', 'failed')
        or not any(edge['type'] == 'records_telemetry' and edge['target'] == events[1]['id'] for edge in events[-1]['links'])
    ):
        raise ValueError('Preservation requires a completed run journal')
    manifest = {'schema': 1, 'integrity_root': events[-1]['hash'], 'files': [], 'git_references': []}
    paths = {}
    for event in events:
        ref = event['metadata']
        if 'path' not in ref or 'storage' not in ref:
            continue
        if ref['storage'] == 'git':
            manifest['git_references'].append({'event_id': event['id'], **ref})
            continue
        if ref.get('kind') == 'symlink' or 'sha256' not in ref:
            raise ValueError('Cannot preserve a non-regular file reference')
        checksum = ref['sha256']
        if len(checksum) != 64 or any(c not in '0123456789abcdef' for c in checksum):
            raise ValueError('Invalid file checksum')
        source = Path(str(journal) + '.files') / checksum if ref.get('snapshot') else Path(ref['path'])
        if not source.is_file() or source.is_symlink() or file_hash(source) != checksum:
            raise ValueError(f'Unavailable file or checksum mismatch for event {event["id"]}')
        member = f'files/{checksum}'
        paths[member] = source
        manifest['files'].append({'event_id': event['id'], 'member': member, **ref})
    target = Path(str(journal) + '.zip')
    with tempfile.NamedTemporaryFile(dir=journal.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(zipfile.ZipInfo('manifest.json'), canonical(manifest))
            archive.writestr(zipfile.ZipInfo('run.jsonl'), b''.join(canonical(event) + b'\n' for event in events))
            for member, source in sorted(paths.items()):
                info = zipfile.ZipInfo(member)
                info.compress_type = zipfile.ZIP_DEFLATED
                with source.open('rb') as incoming, archive.open(info, 'w', force_zip64=True) as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
        with zipfile.ZipFile(temporary) as archive:
            import hashlib

            for member in paths:
                digest = hashlib.sha256()
                with archive.open(member) as stream:
                    while block := stream.read(1024 * 1024):
                        digest.update(block)
                if digest.hexdigest() != member.split('/')[1]:
                    raise ValueError('File changed while packaging; checksum mismatch')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def preserve_to_dataerai(journal, *, client, server, project, collection=None, allocation=None):
    """Upload a bundle using an authenticated, connected Dataerai SDK client.

    The SDK blocks until transfer completion. Keep the returned content ID to
    retrieve this exact version; a retry may create another content version.
    """
    status = client.auth_status()
    if not status.user_email or (status.server_url or '').rstrip('/') != server.rstrip('/'):
        raise ValueError('Dataerai SDK must be authenticated to the requested server')
    bundle = preservation_bundle(journal)
    with zipfile.ZipFile(bundle) as archive:
        root = json.loads(archive.read('manifest.json'))['integrity_root']
    checksum = file_hash(bundle)
    result = client.upload(
        str(bundle),
        title=f'hls4ml preserved run {root}',
        owner_type='project',
        owner_id=project,
        collection_id=collection,
        allocation_id=allocation,
        record_type='dataset',
        tags=['hls4ml', 'provenance', 'preservation'],
        description='Journal, manifest, and external file snapshots; committed source referenced by Git.',
        metadata={'hls4ml_preservation': {'integrity_root': root, 'bundle_sha256': checksum, 'schema': 1}},
    )
    if not result.asset_id or not result.content_id or result.total_bytes != bundle.stat().st_size:
        raise RuntimeError('Dataerai upload did not confirm the complete bundle')
    receipt = {
        'asset_id': result.asset_id,
        'content_id': result.content_id,
        'bundle_sha256': checksum,
        'integrity_root': root,
        'size_bytes': result.total_bytes,
    }
    Path(str(journal) + '.preserved.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt
