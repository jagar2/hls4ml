"""Verify a journal and display workflow status, file references, and graph edges."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hls4ml.provenance import read_journal


def inspect_journal(path):
    events = read_journal(path)
    workflow = events[1]
    completions = [
        event
        for event in events
        if any(link['type'] == 'records_telemetry' and link['target'] == workflow['id'] for link in event['links'])
    ]
    references = [
        dict(id=event['id'], title=event['title'], **event['metadata'])
        for event in events
        if event['metadata'].get('role') in ('input', 'output')
    ]
    edges = []
    for event in events:
        for link in event['links']:
            source, target = event['id'], link['target']
            if link.get('direction') == 'incoming':
                source, target = target, source
            edges.append({'source': source, 'type': link['type'], 'target': target})
    summary = {
        'workflow': workflow['title'],
        'status': completions[-1]['metadata']['status'] if completions else 'incomplete',
        'source': events[0]['metadata'],
        'events': len(events),
        'integrity_root': events[-1]['hash'],
        'activities': [{'id': event['id'], 'title': event['title']} for event in events if event['kind'] == 'simulation'],
        'artifacts': references,
        'edges': edges,
    }
    return events, summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('journal', type=Path)
    parser.add_argument('--dot', type=Path, help='Also write the provenance graph in Graphviz DOT format')
    args = parser.parse_args(argv)
    events, summary = inspect_journal(args.journal)
    if args.dot:
        lines = ['digraph provenance {']
        for event in events:
            label = f'{event["title"]}\n{event["metadata"].get("status", event["kind"])}'
            lines.append(f'  {json.dumps(event["id"])} [label={json.dumps(label)}];')
        for edge in summary['edges']:
            lines.append(
                f'  {json.dumps(edge["source"])} -> {json.dumps(edge["target"])} [label={json.dumps(edge["type"])}];'
            )
        args.dot.write_text('\n'.join([*lines, '}']) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
