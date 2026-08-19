"""Safety: incidents, lifeguard cover, emergency contacts, evacuation plans,
equipment checks, weather warnings and per-student restrictions.

The rule that shapes every model in here
----------------------------------------
**The AI is never the final authority on a safety decision.** Where a model can
carry an AI-sourced opinion (``WeatherWarning.ai_suggested`` /
``ai_rationale``), that opinion is stored in fields that are structurally
separate from the human decision (``acknowledged_by`` / ``acknowledged_at``).
Nothing downstream treats an unacknowledged AI suggestion as a fact: the test is
``WeatherWarning.is_authoritative``, and it is false until a named member of
staff signs it off.
"""
