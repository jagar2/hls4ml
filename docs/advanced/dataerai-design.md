# Dataerai provenance integration design

## Objective and scope

Track hls4ml workflows and their scripts/version history in Dataerai while
referencing Git commits and external artifacts instead of re-uploading files.
This branch is based on upstream `main`, commit
`94922bfe` (full identity recorded in Git), and targets an hls4ml fork.
It changes no Dataerai backend schema, SDK, QA catalog, or shared contracts.

## Decision

Use context-local instrumentation at existing public/shared entry points.
Without an active Run, decorators call the original function directly.
Existing signatures, return values, compilation logic, and backend options
remain intact. All core functionality uses the Python standard library and
hls4ml's existing NumPy dependency; no required Dataerai SDK is introduced.

`_identity` resolves Git and content identities and redacts metadata.
`_journal` durably appends immutable nodes with ordered hash-chain evidence.
`_tracking` owns workflow and step lifecycles, source references, array/model
dependency links, and output inventories. `_sync` validates and replays those
nodes through Dataerai metadata-only assets and relationships. `_github`
observes the metadata of completed GitHub workflow runs. The CLI composes these
pieces for Python, shell, and CI use.

## Record contract and oracle

Every journal node carries its schema, UUID, sequence, predecessor hash,
timestamp, record kind, title, structured metadata, relationships, and hash.
Relationships name only earlier nodes; incoming edges express production or
consumption in the right direction without mutating earlier records.
Start and completion are distinct immutable records. A missing completion is
an incomplete observation, never success.

The API payloads are grounded in Dataerai's `AssetWriteSerializer`,
`AssetsView.post`, and `AssetRelationshipsView.post` in the companion Dataerai
repository: metadata-only create, optional `client_asset_id`, project and
collection ownership, and typed directed relationships. UUIDs include the
destination project/collection and journal identity. Asset conflicts are
errors; only the server's specific identical-relationship response counts
as a successful duplicate. No `/content/` or storage upload API is used.

The independent behavioral checks use real temporary Git repositories,
real hls4ml graph generation, and a local HTTP server that simulates an
ambiguous partially successful synchronization. API fixtures are evidence
about the client contract, not a claim of live service conformance.

## Requirements and regression matrix

| Requirement / invariant | Evidence in new test module |
| --- | --- |
| Source commit/blob/parents; dirty sources distinguished | Git identity, dirty-content, and parent-history tests |
| Original behavior and no IO when disabled | Disabled decorator test; real graph generation test |
| Nested activities and failed runs remain visible | Step failure and command exit-code tests |
| Git source, scripts, array/model identity; output checksums | Script runner, array hash, graph/optimizer/write tests |
| Producer-consumer relationships | Array producer-to-consumer test |
| No file uploads, same IDs on retry, partial-write recovery | Sync payload and actual HTTP retry tests |
| Authorization/conflict failures cannot look successful | 401/403/409/500 and relationship-conflict tests |
| Journal cannot be overwritten or silently altered | Overwrite, hash-chain and incomplete-run tests |
| Secret handling and HTTPS origin restriction | Redaction and unsafe-origin tests |
| All observed GitHub jobs/steps and attempt identity | GitHub metadata observer test |

## Risk, rollout, and recovery

The primary risks are recording overhead, unsupported scientific objects,
retention of externally referenced content, and API/deployment mismatch.
Enable a workflow explicitly, inspect its local journal, then synchronize to
an existing project. Enable the workflow observer separately after default
branch adoption. Roll back by removing the Run wrapper or disabling the
repository variable. Existing hls4ml use stays unchanged.

No arbitrary subprocess tracer, cell-history recorder, automatic training
framework patching, external artifact backup, authenticated signature, or
automatic credential refresh is claimed. The user guide states these capture
boundaries and the way to add explicit steps/artifact references.

## Verification

Test manifest: `test/pytest/test_dataerai_provenance.py` and
`test/pytest/test_dataerai_tutorials.py`.
The initial command-failure test was observed failing before the runner existed.
Local validation on macOS / Python 3.14 passed all 29 tests: 25 integration
checks and four tutorial checks. The focused documentation build passed with
warnings treated as errors. The real
graph test verifies byte-identical generated C++ with recording on and off;
the failed-build test verifies partial-artifact retention without raw log
content. The redirect test verifies bearer credentials are not forwarded.
Repository pre-commit checks passed on edited files. A wheel build succeeded,
and its archive contained all seven provenance modules. The companion
Dataerai catalog check passed (98 feature definitions, 119 scenarios).
The real graph test runs code generation without a licensed
HLS toolchain. The HTTP retry test uses a local server, not a live Dataerai
deployment. Live Dataerai synchronization, automatic GitHub observer deployment, and
licensed synthesis/co-simulation remain unverified until credentials,
destination project, and vendor tools are configured. No frontend was changed.

## Maintaining the documentation and tutorials

The overview links to the tutorials, record semantics, API/CLI reference, and
operations guide. The main hls4ml documentation includes that overview in its
Advanced section. The tutorial page includes the executable script directly
with Sphinx `literalinclude`, so rendered code stays aligned with tested code.

The tutorial tests copy the published scripts into a real temporary Git
repository and run them in subprocesses. They check actual fitted coefficients,
generated HLS C++, input/output relationships, changed-seed comparisons,
failure retention, and rejection of tampered journals. Generated datasets,
models, journals, and HLS projects stay outside version control.

Run the integration and tutorial checks from an environment with this checkout
installed:

```bash
python -m pip install . pytest sphinx myst-parser
python -m pytest -q test/pytest/test_dataerai_provenance.py test/pytest/test_dataerai_tutorials.py
sphinx-build -W --keep-going -b html -c docs/tutorials/dataerai/sphinx docs .dataerai/docs-html
```

The focused documentation configuration builds all six provenance pages,
including their cross-references and source inclusions. Warnings fail the
build. It avoids the unrelated online extensions in the full hls4ml site;
passing this check is not a claim that the entire upstream site was built or
that external websites are reachable. Open
`.dataerai/docs-html/advanced/dataerai.html` to read the rendered guide locally.

The integration workflow runs both test modules on Python 3.10, 3.12, and 3.14,
and builds the focused documentation on Python 3.12. Documentation and tutorial
changes are included in its pull-request path filters.
