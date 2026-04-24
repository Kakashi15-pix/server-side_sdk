# Developer Guide: Server Auth Modules

This file is the server-side quick index. For cross-repo context, also read:
- [cost_analytics-SDK/DEVELOPER_GUIDE.md](../cost_analytics-SDK/DEVELOPER_GUIDE.md)

## 1) What this repo owns

- FastAPI API key verification dependency
- API key lifecycle service
- auth-context-aware rate-limiting middleware
- security policy for key hashing, masking, expiry skew, and cache TTL

## 2) Start reading here

- [auth.py](auth.py)
- [api_keys.py](api_keys.py)
- [rate_limit.py](rate_limit.py)
- [manager.py](manager.py)

## 3) Runtime flow

1. verify_api_key parses Bearer token.
2. HMAC hash is derived and looked up.
3. Revocation and expiry-with-skew checks execute.
4. Auth context is attached to request state.
5. Middleware applies key bucket first, then user fallback.
6. BackendPricingOrchestrator transforms buffered request details into costed analytics records.

## 4) Security rules

1. Never store raw keys.
2. Use HMAC-SHA256 with CA_KEY_HMAC_SECRET.
3. Mask logs as ca_live_****.
4. Keep auth cache TTL <= 60 seconds.
5. Use timezone-aware UTC for expiry comparisons.

## 5) Tests

- Unit tests:
  - [tests/test_auth.py](tests/test_auth.py)
  - [tests/test_api_keys.py](tests/test_api_keys.py)
  - [tests/test_rate_limit.py](tests/test_rate_limit.py)
- Test runner: [test.sh](test.sh)
- Real DB strategy: [docs/auth-db-testing.md](docs/auth-db-testing.md)

## 6) Change checklist

1. Update tests first for policy or behavior changes.
2. Re-run full auth test suite.
3. Check no raw-key leakage in logs/test output.
4. Validate clock-skew boundaries for expiry logic.

## 7) Analytics dashboard handoff

If another developer is implementing the analytics dashboard, start with:
- [docs/analytics-dashboard-handoff.md](docs/analytics-dashboard-handoff.md)

That document contains:
- source-of-truth data flow from SDK to backend
- analytics record contract produced by manager.py
- endpoint plan for dashboard APIs
- schema/query guidance for summary, time-series, top-model, and request drilldown views
