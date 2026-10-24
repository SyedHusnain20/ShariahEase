"""
Optional LangSmith tracing.

`traceable` is LangSmith's decorator when the package is installed, and a
no-op otherwise, so the app never depends on LangSmith being present.

Nothing is sent anywhere unless tracing is switched on through the environment:

    LANGSMITH_TRACING=true
    LANGSMITH_API_KEY=<your key>
    LANGSMITH_PROJECT=ShariahEase          # optional, groups runs in the UI

Chat questions and Zakat-related messages can contain personal financial
details. To keep them out of traces while still seeing timings, errors and
run structure, also set:

    LANGSMITH_HIDE_INPUTS=true
    LANGSMITH_HIDE_OUTPUTS=true
"""

try:
    from langsmith import traceable as _traceable
except Exception:  # package missing or broken: run untraced
    _traceable = None


def traceable(**kwargs):
    """Use as @traceable(name="...", run_type="chain", tags=[...])."""
    if _traceable is None:
        def _identity(fn):
            return fn
        return _identity
    return _traceable(**kwargs)
