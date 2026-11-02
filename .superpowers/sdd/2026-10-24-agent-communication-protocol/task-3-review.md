# Task 3 独立审查结果

审查区间：`cb0bc6f07132564c511a3113e7966ac5123dbd9f..8950d41b5bae240bc579e8b86eca3604d7802177`
Review package：`review-cb0bc6f..8950d41.diff`

## Spec compliance

部分符合，需修复后通过。Gateway 显式候选路由、错误不回显消息、freeform 原文透传、解析成功后生成规范 JSON context 符合要求。

## Important finding

`src/agentcodec/agentwire/prompts.py:292-304,335-343` 计算 `dictionary_identity_for(snapshot)` 后丢弃身份结果；附加 glossary 只带 `user_id`、`version` 和 `aliases`。与此同时，AWP1 固定格式说明要求 payload identity（包括 SHA-256）匹配所选 glossary。`build_direct_model_request` 因此仍会将所选 glossary 附在字典 hash 不同的 AWP1 payload 后，且请求中没有完整 glossary hash 可供比较。这使错误 glossary 与 AWP1 wire payload 配对，属于词典身份绑定重要缺陷。

修复要求：在直接请求里以确定、可读的形式包含所选词典完整 identity `[user_id, dictionary_version, sha256]`；对 AWP1 payload 中 identity 与显式所选 identity 不一致的情况 fail-closed；补充先失败后通过的合成回归测试，覆盖 mismatch 拒绝和匹配成功。保留现有候选语法与错误脱敏，不得回显 payload/glossary。不得改变 codec、parser 或 Task 1/2 行为。

## Critical / Minor

无。

## Cannot verify

审查者未重跑实现者报告的 focused 12 passed / full 1684 passed；结果归因于实现者报告。审查者只聚焦查看 `parse_input` 的显式格式分派；AC1、AC2 与 AWP1 decoder 严格性依赖 Task 1/2 契约。

## Verdict

Task quality: Needs fixes。Task 4 阻塞，直至该 Important 修复通过新的独立 scoped re-review。
