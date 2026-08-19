"""AI Development Terminal.

An in-application console where an AI agent can inspect the codebase, propose
changes and run development commands — inside a hard security boundary defined
in :mod:`apps.ai_terminal.security`.

The AI never gets a shell. It proposes; a human approves; the system executes a
validated argument vector with no shell interpretation.
"""
