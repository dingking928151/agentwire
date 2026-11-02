# Task 5 fix round 1 scoped re-review

Diff range: `b2058d98811b3b56f191dd3b8c27c3076a06a1bf..fedf0c422a08721b9241bc4a32eedf243a89d469`; package `review-b2058d9..fedf0c4.diff`.

1. Important — ADDRESSED. Gateway decode failure retains known request/transport cost, sets unknown response/retry/fallback, model input/response/lifecycle measures to null, marks comparison `cannot_compare`, and is excluded from baseline comparison (`benchmark.py:721-724,731-736,777-787,803-831,907-925`). Regression covers retaining known attempt cost and preventing comparison (`tests/test_agentwire_benchmark.py:785-812`). No synthetic retry/fallback is performed (`task-5-report.md:103-105`).
2. Important — ADDRESSED. Incomplete cold protocol/dictionary history propagates to unknown state; associated history/setup/lifecycle cost is null and comparisons unavailable (`benchmark.py:490,500,745-766,803-831`). Tests cover both protocol and dictionary cases (`tests/test_agentwire_benchmark.py:815-867`).

New breakage: None. Out-of-scope observation: carry forward non-blocking Minor maintainability note that `benchmark.py` is large; consider splitting after behavior stabilizes. Does not extend this fix loop.

Tests were not rerun by reviewer. Implementer report records RED 3 failed, fix GREEN 3 passed, focused 25 passed, full 1722 passed; not independently verified.

Fix-round verdict: all pending findings addressed; no new Critical/Important issue.
