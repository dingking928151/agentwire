### Task 7：发布固定协议文档并验证旧兼容性

**Files:** 新增 `docs/agentwire-compact-profile-v1.md`、`tests/test_agentwire_compatibility.py`；修改 `README.md`。

**Interfaces:** 消费完整 AWP1 codec、gateway、benchmark 和 CLI 契约；不新增 wire/API 行为。

- [ ] **Step 1：写兼容性失败测试。** `test_legacy_ac1_default_and_ac2_opt_in_are_unchanged` 锁定现有 API/golden；`test_legacy_dispatcher_does_not_guess_awp1` 断言既有 `decode_payload("AWP1|...")` 仍要求 fallback；`test_legacy_cli_and_report_versions_remain_stable` 验证旧 benchmark/evaluate/adaptive 命令仍注册且版本不变。
- [ ] **Step 2：验证 RED。** 运行 `.venv/bin/python -m pytest tests/test_agentwire_compatibility.py -q`，确认 AWP1 导入或集成可能回归时能被测试捕获；若旧行为已满足，测试应在新增前绿，则明确记录其作为兼容守卫，不伪称 RED。
- [ ] **Step 3：写规范文档。** 固定说明 11 个字段顺序、枚举表、JSON framing/escaping、canonical dictionary hash、能力协商/最大 UTF-8 bytes、精确引用、reason codes、fail-closed、gateway/model-direct 成本边界和“不是认证/授权/模型理解”限制；README 仅给本地合成命令和清楚免责声明，并分别标明人工 freeform 与固定 labelled-field 两种自然语言对照；不把 labelled-field token 差异称为真实自然语言节省。
- [ ] **Step 4：最终验证。** 运行 `.venv/bin/python -m pytest tests/test_agentwire_compatibility.py -q`，再运行 `.venv/bin/python -m pytest -q`；预期全部通过，不安装依赖、不下载 tokenizer、不连接外部服务。运行 `git diff --check` 并复核旧文件改动仅限 `cli.py`/`README.md` 的明确增量。
- [ ] **Step 5：自审并提交。** 仅提交两份文档和兼容性测试，提交信息 `docs: document AgentWire v1 and preserve legacy behavior`。随后按用户指定流程做完整分支独立审查；不 merge/push。
