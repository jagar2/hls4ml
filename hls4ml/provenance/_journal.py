"""Durable append-only provenance nodes with a verifiable local hash chain."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ._identity import canonical, digest, redact, summarize


class Journal:
    def __init__(self, path):
        self.path = Path(path).absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open('x', encoding='utf-8')
        os.chmod(self.path, 0o600)
        self._lock = threading.RLock()
        self._previous = None
        self._sequence = 0

    def append(self, kind, title, metadata, links=()):
        with self._lock:
            event = {
                'schema': 'hls4ml.dataerai/v1',
                'id': str(uuid.uuid4()),
                'sequence': self._sequence,
                'previous': self._previous,
                'time': datetime.now(timezone.utc).isoformat(),
                'kind': kind,
                'title': redact(title),
                'metadata': redact(summarize(metadata)),
                'links': list(links),
            }
            event['hash'] = digest(event)
            self._stream.write(canonical(event).decode() + '\n')
            self._stream.flush()
            os.fsync(self._stream.fileno())
            self._previous = event['hash']
            self._sequence += 1
            return event['id']

    def close(self):
        self._stream.close()


def read_journal(path):
    events, previous, known = [], None, set()
    with Path(path).open(encoding='utf-8') as stream:
        for sequence, line in enumerate(stream):
            event = json.loads(line)
            fingerprint = event.pop('hash')
            if event['schema'] != 'hls4ml.dataerai/v1' or event['sequence'] != sequence:
                raise ValueError('Invalid journal schema or sequence')
            if event['previous'] != previous or digest(event) != fingerprint:
                raise ValueError('Journal hash chain is invalid')
            if event['id'] in known or any(link['target'] not in known for link in event['links']):
                raise ValueError('Journal contains duplicate identities or dangling links')
            uuid.UUID(event['id'])
            event['hash'] = fingerprint
            events.append(event)
            known.add(event['id'])
            previous = fingerprint
    if not events:
        raise ValueError('Journal is empty')
    return events
