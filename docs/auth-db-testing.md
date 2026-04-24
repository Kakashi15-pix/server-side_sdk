# Auth Module Real DB Testing Guide

Use this guide when you want to validate auth behavior against a real database, not in-memory test doubles.

## Why this exists

Unit tests in tests/test_auth.py, tests/test_api_keys.py, and tests/test_rate_limit.py are fast and should stay in place.
Real DB integration tests add confidence for:
- actual persistence behavior
- transaction boundaries
- schema and query correctness
- time-based edge handling (expiry and clock skew)

## Recommended test strategy

1. Keep unit tests as the default local run.
2. Add integration tests under tests/integration/ for DB-backed flows.
3. Run integration tests in CI for merge/release gates.
4. Never run integration tests against dev or production databases.

## Environment requirements

- Python 3.9+
- pytest
- a PostgreSQL test instance (container or dedicated local test DB)
- CA_KEY_HMAC_SECRET set for auth hashing tests

Optional but recommended:
- testcontainers (ephemeral DB)
- pytest-asyncio (if repository/DB layer is async)

## Suggested folder structure

- tests/integration/conftest.py
- tests/integration/test_auth_db.py
- tests/integration/test_api_keys_db.py
- tests/integration/test_rate_limit_db.py

## Integration fixture pattern

Use two layers of fixtures:

1. Session fixture
- starts ephemeral Postgres
- applies schema/migrations
- yields database connection URL
- tears down DB at end of session

2. Function fixture
- opens transaction for each test
- runs test
- rolls back after test

This keeps tests isolated and repeatable.

## What to validate with a real DB

### 1) Security at rest

- raw key is never persisted
- only HMAC-SHA256 hash is stored
- stored hash length/shape matches expected hex digest
- key masking paths never expose full keys in logs or test output

### 2) Clock skew edge behavior

Validate all three states using stored expiry values:
- expires_at clearly in future -> accepted
- expires_at exactly at boundary (now - skew) -> rejected
- expires_at just inside skew window -> accepted

### 3) Latency and read path behavior

Measure auth verification end-to-end with the real DB:
- cold path (DB lookup) stays within an agreed local threshold
- warm path (cache hit) is significantly faster than cold path

Do not assert extremely tight numbers in CI. Use practical thresholds to avoid flaky tests.

## Example developer workflow

1. Start test database (or let fixture/container start it).
2. Export CA_KEY_HMAC_SECRET for test run.
3. Run unit tests first.
4. Run integration tests second.
5. If integration tests fail, inspect schema mismatch, missing migration, or time-zone handling first.

## CI recommendation

- Job 1: unit suite (fast, always)
- Job 2: integration suite with ephemeral Postgres
- Block merges only when both pass

## Non-negotiable safety rules

- never point integration tests at shared environments
- never print raw keys
- keep cache TTL in tests aligned with production policy (<= 60s)
- ensure all expiry comparisons are timezone-aware UTC
