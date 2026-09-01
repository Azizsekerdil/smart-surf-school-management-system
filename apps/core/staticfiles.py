"""Static file storage used by ``collectstatic``.

Vendored third-party bundles (e.g. ``static/vendor/chartjs/chart.umd.js``)
end with a ``//# sourceMappingURL=...`` comment that points at a source map
we deliberately do not ship. Django's manifest storage rewrites every such
reference to its hashed name and *fails the whole collectstatic run* when the
referenced file does not exist.

This subclass downgrades a missing referenced file from a hard error to
"keep the original reference untouched", which is the behaviour every other
static server has: a missing source map only matters when the browser dev
tools are open.
"""

from __future__ import annotations

from whitenoise.storage import CompressedManifestStaticFilesStorage


class ForgivingCompressedManifestStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """Manifest storage that tolerates references to files we do not ship."""

    def hashed_name(self, name, content=None, filename=None):
        try:
            return super().hashed_name(name, content, filename)
        except ValueError:
            # Referenced file (usually a *.map) is absent: leave the original
            # reference in place instead of aborting collectstatic.
            return name
