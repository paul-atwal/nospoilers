"""AWS Lambda Function URL entry point."""

from __future__ import annotations

import os

WARM_EVENT_KEY = "nospoil_warm"

_lambda_handler = None


def _build_handler():
    from mangum import Mangum
    from .http import create_app

    return Mangum(create_app(), lifespan="off")


def lambda_handler(event, context):
    global _lambda_handler
    if _lambda_handler is None:
        _lambda_handler = _build_handler()
    if isinstance(event, dict) and event.get(WARM_EVENT_KEY) is True:
        # Scheduled keep-warm ping: the environment is initialized; no request.
        return {"warm": True}
    return _lambda_handler(event, context)


# Build during Lambda's init phase rather than on the first request. Imports
# elsewhere (tests, local tools) stay free of runtime configuration.
if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    _lambda_handler = _build_handler()


__all__ = ["WARM_EVENT_KEY", "lambda_handler"]
