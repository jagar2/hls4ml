"""Shared opt-in switches for CLI commands and runnable tutorials."""

import os
import warnings
from contextlib import contextmanager, nullcontext

from ._preserve import preservation_bundle, preserve_to_dataerai
from ._tracking import _CURRENT, Run


def add_arguments(parser, *, enabled=False):
    switch = parser.add_mutually_exclusive_group()
    switch.add_argument('--dataerai', action='store_true', dest='dataerai', help='Record workflow provenance locally')
    switch.add_argument('--no-dataerai', action='store_false', dest='dataerai', help='Disable provenance recording')
    parser.set_defaults(dataerai=True if enabled else None)
    parser.add_argument('--dataerai-preserve', action='store_true', help='Snapshot recorded files and build a run bundle')
    parser.add_argument('--dataerai-sync', action='store_true', help='Publish metadata and relationships to Dataerai')
    parser.add_argument('--dataerai-upload', action='store_true', help='Preserve and upload a run bundle using the SDK')
    parser.add_argument('--dataerai-journal', help='New journal path')
    parser.add_argument('--dataerai-repo', default='.', help='Git source checkout')
    parser.add_argument('--dataerai-input', action='append', default=[], help='Input file/directory to record')
    parser.add_argument('--dataerai-output', action='append', default=[], help='Output file/directory to record at exit')


def publish(journal, *, sync=False, upload=False, preserve=False):
    """Finalize explicitly requested effects; failures retain local evidence."""
    from .__main__ import _client

    outcome = {}
    if preserve or upload:
        outcome['bundle'] = str(preservation_bundle(journal))
    receipt = None
    if upload:
        try:
            from dataerai import DataeraiClient
        except ImportError as exc:
            raise RuntimeError(
                'Dataerai upload requires the optional dataerai SDK and authenticated transfer daemon'
            ) from exc
        with DataeraiClient(binary_path=os.environ.get('DATAERAI_BINARY')) as client:
            receipt = preserve_to_dataerai(
                journal,
                client=client,
                server=os.environ['DATAERAI_SERVER'],
                project=os.environ['DATAERAI_PROJECT_ID'],
                collection=os.environ.get('DATAERAI_COLLECTION_ID'),
                allocation=os.environ.get('DATAERAI_ALLOCATION_ID'),
            )
        outcome.update(receipt)
    if sync:
        client = _client()
        result = client.sync(journal)
        outcome.update(result)
        if receipt:
            status, body = client.request(
                'POST',
                f'/api/assets/{result["run_asset_id"]}/relationships/',
                {'to_asset_id': receipt['asset_id'], 'type': 'produces'},
            )
            if status not in (200, 201) and not (
                status == 409 and body.get('detail') == 'An identical relationship already exists.'
            ):
                raise RuntimeError('Preserved content uploaded, but linking it to the workflow failed; retry publication')
    return outcome or None


@contextmanager
def workflow(args, name, *, repository=None, journal=None, parameters=None):
    if args.dataerai is False and (args.dataerai_preserve or args.dataerai_sync or args.dataerai_upload):
        raise ValueError('--no-dataerai cannot be combined with preservation or publication flags')
    enabled = args.dataerai or args.dataerai_preserve or args.dataerai_sync or args.dataerai_upload
    inherited = _CURRENT.get() if not enabled and args.dataerai is None else None
    run = inherited or Run(
        name,
        enabled=enabled,
        preserve=args.dataerai_preserve or args.dataerai_upload,
        repository=repository or args.dataerai_repo,
        journal=args.dataerai_journal or journal,
        parameters=parameters,
    )

    def finish():
        if enabled and run.id and inherited is None:
            publish(run.path, sync=args.dataerai_sync, upload=args.dataerai_upload, preserve=args.dataerai_preserve)

    try:
        with nullcontext(run) if inherited else run:
            for path in args.dataerai_input:
                run.artifacts(path, role='input')
            try:
                yield run
            finally:
                for path in args.dataerai_output:
                    run.artifacts(path)
    except BaseException:
        try:
            finish()
        except Exception as exc:
            warnings.warn(
                f'Dataerai publication failed ({type(exc).__name__}); local journal retained at {run.path}', stacklevel=2
            )
        raise
    else:
        finish()
