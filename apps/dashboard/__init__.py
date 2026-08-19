"""Operational dashboard and global search.

This app owns no models. It composes a single operations screen out of every
other module through lazy ``apps.get_model`` lookups, so a module that has not
landed yet (or has no rows) degrades to a neutral "no data yet" tile instead of
raising.
"""
