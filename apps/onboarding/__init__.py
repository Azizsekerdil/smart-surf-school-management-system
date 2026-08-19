"""First-run setup wizard.

A fresh installation knows nothing about the school it is about to run: not its
name, not its currency, not which break it teaches at. This module asks for that
once, in nine short steps, and on Finish it writes the answers into the records
the rest of the system actually reads — ``core.SystemSetting`` rows and the
primary ``locations.SurfSpot``.

Nothing here is mandatory. The wizard can be skipped at any point and every
answer can be changed later from Settings; the dashboard simply keeps showing a
dismissible banner until setup is marked done.
"""
