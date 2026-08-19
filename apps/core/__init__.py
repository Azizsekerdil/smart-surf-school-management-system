"""Core foundation: abstract models, mixins, validators, logging and shared utilities.

Every other app builds on the contracts defined here. Nothing in `core` may
import from another `apps.*` package, so it stays dependency-free.
"""
