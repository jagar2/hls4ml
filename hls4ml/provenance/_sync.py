"""Replay immutable journal nodes through Dataerai's metadata-only REST API."""

from __future__ import annotations

import json
import uuid
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ._identity import canonical
from ._journal import read_journal


class SyncError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Dataerai:
    def __init__(self, server, token, project, collection=None):
        parsed = urlsplit(server)
        if (
            parsed.scheme != 'https'
            and not (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1'))
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ('', '/')
        ):
            raise ValueError('Dataerai server must be an HTTPS origin (HTTP is allowed only on localhost)')
        if not token or '\n' in token or '\r' in token:
            raise ValueError('A valid DATAERAI_TOKEN is required')
        self.server = server.rstrip('/')
        self.token = token
        self.project = str(uuid.UUID(project))
        self.collection = str(uuid.UUID(collection)) if collection else None
        self._opener = build_opener(_NoRedirect())

    def request(self, method, path, payload):
        request = Request(
            self.server + path,
            data=canonical(payload),
            method=method,
            headers={'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'},
        )
        try:
            with self._opener.open(request, timeout=30) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            try:
                body = json.loads(exc.read())
            except ValueError:
                body = {}
            return exc.code, body

    def sync(self, path, *, allow_incomplete=False):
        events = read_journal(path)
        if len(events) < 2 or events[1]['kind'] != 'simulation':
            raise ValueError('Journal has no workflow run')
        if not allow_incomplete and not (
            events[-1]['metadata'].get('status') in ('succeeded', 'failed')
            and any(
                edge['type'] == 'records_telemetry' and edge['target'] == events[1]['id'] for edge in events[-1]['links']
            )
        ):
            raise ValueError('Run is incomplete; use allow_incomplete=True to publish interrupted evidence')
        namespace = uuid.uuid5(uuid.NAMESPACE_URL, f'{self.server}/{self.project}/{self.collection}')
        ids = {event['id']: str(uuid.uuid5(namespace, f'{event["id"]}:{event["hash"]}')) for event in events}
        for event in events:
            identifier = ids[event['id']]
            payload = {
                'client_asset_id': identifier,
                'title': f'{event["title"][:180]} [{identifier}]',
                'description': 'hls4ml workflow provenance; Git/external references, no file upload',
                'record_type': event['kind'],
                'owner_type': 'project',
                'owner_id': self.project,
                'metadata': {'hls4ml_provenance': event},
                'tags': ['hls4ml', 'provenance'],
                'collection_id': self.collection,
            }
            status, body = self.request('POST', '/api/assets/', payload)
            if status not in (200, 201) or str(body.get('id')) != identifier:
                raise SyncError(f'Dataerai asset synchronization failed (HTTP {status}); journal retained at {path}')
            for edge in event['links']:
                source, target = identifier, ids[edge['target']]
                if edge.get('direction') == 'incoming':
                    source, target = target, source
                status, body = self.request(
                    'POST', f'/api/assets/{source}/relationships/', {'to_asset_id': target, 'type': edge['type']}
                )
                if status not in (200, 201) and not (
                    status == 409 and body.get('detail') == 'An identical relationship already exists.'
                ):
                    raise SyncError(
                        f'Dataerai relationship synchronization failed (HTTP {status}); journal retained at {path}'
                    )
        return {'assets': len(events), 'run_asset_id': ids[events[1]['id']], 'integrity_root': events[-1]['hash']}
