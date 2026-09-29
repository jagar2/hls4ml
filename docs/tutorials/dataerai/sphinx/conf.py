"""Build the provenance guide and tutorials without unrelated online extensions."""

from pathlib import Path

project = 'hls4ml Dataerai provenance'
extensions = ['myst_parser']
root_doc = 'advanced/dataerai'
html_theme = 'alabaster'

docs = Path(__file__).resolve().parents[3]
pages = {
    'dataerai.md',
    'dataerai-tutorials.md',
    'dataerai-records.md',
    'dataerai-reference.md',
    'dataerai-operations.md',
    'dataerai-design.md',
}
exclude_patterns = [
    '_build/**',
    *[
        path.relative_to(docs).as_posix()
        for path in docs.rglob('*')
        if path.suffix in ('.md', '.rst') and not (path.parent == docs / 'advanced' and path.name in pages)
    ],
]
