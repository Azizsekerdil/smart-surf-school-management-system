"""Surf conditions: what the ocean is doing, and whether a group may go in it.

The module has three layers, deliberately separated:

* **Providers** (:mod:`apps.surf_conditions.providers`) talk to the outside
  world. They are the only code that knows an HTTP endpoint exists, they never
  raise, and they return one uniform ``ConditionSnapshot``.
* **Models** store what was observed, so a decision taken last August can still
  be explained this August.
* **Services** turn a snapshot into a *computed* surf score per surf level. The
  score is arithmetic over the thresholds in :mod:`apps.core.enums` — never a
  language model. The AI may narrate the numbers; it never produces them.
"""
