"""Analytics: the numbers a surf school runs on.

Three layers, deliberately separated:

``statistics.py``  pure maths over numeric sequences — no database, no Django
                   models, no AI. Everything in here is unit-tested against
                   hand-computed values.
``services.py``    metric functions that read other modules through lazy
                   ``apps.get_model`` lookups, so analytics never hard-depends
                   on a module and degrades to a ``no_data`` flag instead of a
                   500 when one is empty or absent.
``views.py``       one dashboard that composes the above, plus a CSV export.

The AI narrative on the dashboard is *decoration on top of already-computed
numbers*: the model is handed the finished figures and asked to write prose
about them. It never produces a number, and when no provider is configured the
section simply does not render.
"""
