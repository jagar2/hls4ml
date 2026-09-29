"""Observe completed GitHub Actions runs without executing their checkout or logs."""

from __future__ import annotations

import json
import re
from urllib.request import Request, build_opener

from ._journal import Journal
from ._sync import _NoRedirect
from ._tracking import link


def capture_github_run(repository, run_id, token, journal):
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repository) or not str(run_id).isdigit():
        raise ValueError('Expected GitHub owner/repository and numeric run ID')
    opener = build_opener(_NoRedirect())

    def get(path):
        request = Request(
            f'https://api.github.com/repos/{repository}/{path}',
            headers={
                'Authorization': f'Bearer {token}',
                'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28',
            },
        )
        with opener.open(request, timeout=30) as response:
            return json.load(response)

    def pages(path, key):
        page = 1
        while True:
            batch = get(f'{path}?per_page=100&page={page}')[key]
            yield from batch
            if len(batch) < 100:
                return
            page += 1

    run = get(f'actions/runs/{run_id}')
    if run['status'] != 'completed':
        raise ValueError('Only completed workflow runs can be captured')
    commit = get(f'commits/{run["head_sha"]}')
    output = Journal(journal)
    try:
        source = output.append(
            'software_code',
            'GitHub workflow source',
            {
                'repository': f'https://github.com/{repository}',
                'commit': run['head_sha'],
                'parents': [parent['sha'] for parent in commit['parents']],
                'workflow_path': run['path'],
                'branch': run['head_branch'],
                'commit_url': commit['html_url'],
            },
        )
        workflow = output.append(
            'simulation',
            run['name'],
            {
                'status': 'running',
                'run_id': run['id'],
                'attempt': run['run_attempt'],
                'event': run['event'],
                'actor': run['actor']['login'],
                'url': run['html_url'],
                'started_at': run.get('run_started_at'),
                'conclusion': run['conclusion'],
            },
            [link(source, 'implements')],
        )
        for job in pages(f'actions/runs/{run_id}/attempts/{run["run_attempt"]}/jobs', 'jobs'):
            job_id = output.append(
                'simulation',
                job['name'],
                {
                    key: job.get(key)
                    for key in (
                        'id',
                        'status',
                        'conclusion',
                        'started_at',
                        'completed_at',
                        'html_url',
                        'runner_name',
                        'runner_group_name',
                        'labels',
                    )
                },
                [link(workflow, 'step_of')],
            )
            for step in job.get('steps', []):
                output.append('log', step['name'], step, [link(job_id, 'step_of')])
        for artifact in pages(f'actions/runs/{run_id}/artifacts', 'artifacts'):
            output.append(
                'dataset',
                artifact['name'],
                {
                    key: artifact.get(key)
                    for key in (
                        'id',
                        'size_in_bytes',
                        'digest',
                        'expired',
                        'created_at',
                        'expires_at',
                        'archive_download_url',
                        'workflow_run',
                    )
                },
                [{'target': workflow, 'type': 'produces', 'direction': 'incoming'}],
            )
        output.append(
            'log',
            'GitHub workflow finished',
            {
                'status': 'succeeded' if run['conclusion'] == 'success' else 'failed',
                'conclusion': run['conclusion'],
                'updated_at': run['updated_at'],
                'logs_url': f'https://api.github.com/repos/{repository}/actions/runs/{run_id}/logs',
                'coverage': 'GitHub run, job, and step metadata; internal commands require the instrumented runner',
            },
            [link(workflow, 'records_telemetry')],
        )
    finally:
        output.close()
    return journal
