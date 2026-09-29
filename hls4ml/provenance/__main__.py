"""Record Python entry points or commands, verify journals, and replay to Dataerai."""

from __future__ import annotations

import argparse
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

from . import Run, read_journal
from ._sync import Dataerai


def _client():
    return Dataerai(
        os.environ.get('DATAERAI_SERVER', ''),
        os.environ.get('DATAERAI_TOKEN', ''),
        os.environ.get('DATAERAI_PROJECT_ID', ''),
        os.environ.get('DATAERAI_COLLECTION_ID'),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    record = commands.add_parser('run', help='Run a command or a Python script/module with provenance')
    record.add_argument('--repo', default='.')
    record.add_argument('--name', default='hls4ml workflow')
    record.add_argument('--journal')
    record.add_argument('--allow-dirty', action='store_true')
    record.add_argument('--sync', action='store_true')
    record.add_argument('--script', help='Python script executed in this process to capture hls4ml operations')
    record.add_argument('--module', help='Python module executed in this process')
    record.add_argument('--input', action='append', default=[])
    record.add_argument('--output', action='append', default=[])
    record.add_argument('command', nargs=argparse.REMAINDER)
    sync = commands.add_parser('sync')
    sync.add_argument('journal')
    sync.add_argument('--allow-incomplete', action='store_true')
    verify = commands.add_parser('verify')
    verify.add_argument('journal')
    github = commands.add_parser('github-run', help='Observe a completed GitHub workflow, its jobs, steps, and artifacts')
    github.add_argument('--repo', required=True)
    github.add_argument('--run-id', required=True)
    github.add_argument('--journal', required=True)
    args = parser.parse_args(argv)
    if args.action == 'github-run':
        from ._github import capture_github_run

        capture_github_run(args.repo, args.run_id, os.environ['GITHUB_TOKEN'], args.journal)
        return 0
    if args.action == 'verify':
        events = read_journal(args.journal)
        print(json.dumps({'events': len(events), 'integrity_root': events[-1]['hash']}))
        return 0
    if args.action == 'sync':
        print(json.dumps(_client().sync(args.journal, allow_incomplete=args.allow_incomplete)))
        return 0
    if args.script and args.module:
        parser.error('--script and --module are mutually exclusive')
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not args.script and not args.module and not command:
        parser.error('Provide --script, --module, or a command after --')
    client = _client() if args.sync else None
    run = Run(args.name, repository=args.repo, journal=args.journal, allow_dirty=args.allow_dirty)
    code = 0
    try:
        with run:
            for path in args.input:
                run.artifact(path, role='input')
            try:
                if args.script or args.module:
                    script = str(Path(args.script).absolute()) if args.script else None
                    with run.step(
                        'python', parameters={'script': script, 'module': args.module, 'argv': command}, code=script
                    ):
                        old_argv, old_path = sys.argv, sys.path[:]
                        try:
                            sys.argv = [script or args.module, *command]
                            sys.path.insert(0, str(Path(script).parent) if script else os.getcwd())
                            if script:
                                runpy.run_path(script, run_name='__main__')
                            else:
                                runpy.run_module(args.module, run_name='__main__', alter_sys=True)
                        finally:
                            sys.argv, sys.path = old_argv, old_path
                else:
                    run.command(command)
            finally:
                for path in args.output:
                    if Path(path).is_file():
                        run.artifact(path)
    except subprocess.CalledProcessError as exc:
        code = exc.returncode if exc.returncode >= 0 else 128 - exc.returncode
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        if client and run.id:
            print(json.dumps(client.sync(run.path)))
    print(f'Provenance journal: {run.path}', file=sys.stderr)
    return code


if __name__ == '__main__':
    sys.exit(main())
