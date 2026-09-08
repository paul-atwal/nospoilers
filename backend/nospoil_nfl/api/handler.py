"""Lazy AWS Lambda Function URL entry point."""

from __future__ import annotations

_lambda_handler = None


def lambda_handler(event, context):
    global _lambda_handler
    if _lambda_handler is None:
        from mangum import Mangum
        from .http import create_app

        _lambda_handler = Mangum(create_app(), lifespan="off")
    return _lambda_handler(event, context)


__all__ = ["lambda_handler"]
