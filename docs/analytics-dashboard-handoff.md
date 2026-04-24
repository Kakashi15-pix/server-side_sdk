# Analytics Dashboard Handoff (Server-Side)

This document is for the developer building the analytics dashboard.
It explains where analytics data comes from in the current codebase and what to implement first.

## 1) Source-of-truth modules

Start here in order:
1. [manager.py](../manager.py)
2. [auth.py](../auth.py)
3. [rate_limit.py](../rate_limit.py)
4. [api_keys.py](../api_keys.py)

Cross-repo producer path:
1. [cost_analytics-SDK/my-sdk/src/pricing/interceptor.py](../../cost_analytics-SDK/my-sdk/src/pricing/interceptor.py)
2. [cost_analytics-SDK/my-sdk/src/pricing/aggregator.py](../../cost_analytics-SDK/my-sdk/src/pricing/aggregator.py)

## 2) Real runtime data flow

1. SDK interceptor records request payload metadata and lightweight fields.
2. RequestDetailsBuffer batches items and flushes on timer or threshold.
3. BackendPricingOrchestrator processes each request.
4. process_request resolves provider/model/usage and computes costs.
5. Resulting costed record is emitted through on_persist callback.
6. Dashboard APIs should read from persisted records produced by that callback.

## 3) Analytics record contract (from process_request)

Output record shape produced in [manager.py](../manager.py):

- request_id
- timestamp (ISO string)
- provider
- model
- stop_reason
- usage:
  - input_tokens
  - output_tokens
  - cache_creation_tokens
  - cache_read_tokens
- cost:
  - input_cost
  - output_cost
  - cache_creation_cost
  - cache_read_cost
  - total_cost
- metadata (raw request context)

This is the canonical backend contract for dashboard aggregation.

## 4) What to build first (minimum dashboard backend)

Implement these endpoints before UI polishing:

1. GET /analytics/summary
- returns total_cost, total_requests, total_input_tokens, total_output_tokens
- supports range filters: start, end
- optional filters: provider, model

2. GET /analytics/timeseries
- returns buckets for total_cost and request_count
- params: start, end, interval (hour|day)
- optional filters: provider, model

3. GET /analytics/top-models
- returns top models by cost and requests
- params: start, end, limit

4. GET /analytics/requests
- paginated request-level rows for drilldown table
- includes request_id, timestamp, provider, model, total_cost, token fields, stop_reason

## 5) Storage guidance

Persist costed records from orchestrator callback into a table (example):

- analytics_events
  - id (pk)
  - request_id (unique)
  - timestamp (indexed)
  - provider (indexed)
  - model (indexed)
  - stop_reason
  - input_tokens
  - output_tokens
  - cache_creation_tokens
  - cache_read_tokens
  - input_cost
  - output_cost
  - cache_creation_cost
  - cache_read_cost
  - total_cost
  - metadata (jsonb)

Recommended indexes:
- (timestamp)
- (provider, timestamp)
- (model, timestamp)

## 6) Auth and tenancy for dashboard APIs

Use existing auth dependency in [auth.py](../auth.py):
- require verify_api_key on dashboard endpoints
- scope queries to request identity (user_id or api_key_id) when multi-tenant
- never expose rows across tenants

## 7) Query patterns

1. Summary
- SUM(total_cost), COUNT(*), SUM(input_tokens), SUM(output_tokens)
- WHERE timestamp between start/end and tenant filter

2. Time-series
- GROUP BY time_bucket(interval, timestamp)
- aggregate SUM(total_cost), COUNT(*)

3. Top models
- GROUP BY model
- ORDER BY SUM(total_cost) DESC

4. Request drilldown
- ORDER BY timestamp DESC
- LIMIT/OFFSET or cursor pagination

## 8) UI view mapping

1. KPI cards
- total cost
- request count
- avg cost per request
- total tokens

2. Time-series chart
- cost trend
- request volume trend

3. Provider/model breakdown
- stacked bar or table by provider/model

4. Request table
- drilldown for outliers and debugging

## 9) Security and correctness constraints

1. Never store or display raw API keys.
2. Keep metadata sanitized before rendering in UI.
3. Use UTC consistently for filtering and chart buckets.
4. Preserve cost precision (decimal/NUMERIC in DB, not float-only display math).

## 10) Definition of done

1. Endpoints return correct aggregates for filtered windows.
2. Dashboard renders summary, time-series, top-model, and request table.
3. Access control verified via auth dependency.
4. No raw key leakage in logs or response payloads.
5. Query latency remains acceptable for default 7-day window.
