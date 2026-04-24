#!/bin/bash
# Run server-side auth module tests.

set -e

echo "Running server-side auth tests..."

pip install -U pytest fastapi starlette
export CA_KEY_HMAC_SECRET="test_secret"
pytest tests/ -v

echo "✓ Server-side auth tests complete"
