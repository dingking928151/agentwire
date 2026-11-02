# Task 4 fix round 1 scoped re-review

Fix diff: `994c6337ce74796c1cd751b3174eec96072512e6..c23cbe654ceedcc73e3fce6d22bd5691f7d9c383` (`review-994c633..c23cbe6.diff`).

- Important — ADDRESSED: `src/agentcodec/agentwire/contracts.py:231` now requires at least one `CostPart`. `tests/test_agentwire_costs.py:210-224` rejects missing parts and confirms `CostPart(text="")` remains an explicit valid empty text part.
- Minor — ADDRESSED: `tests/test_agentwire_costs.py:241-246` directly verifies unknown `None` input yields `cannot_compare` and `first_strict_win is None`.
- New breakage: None.
- Out-of-scope observations: None.
- Fix-round verdict: passed. Reviewer did not rerun tests; focused 11 and full 1697 are implementer-reported.
