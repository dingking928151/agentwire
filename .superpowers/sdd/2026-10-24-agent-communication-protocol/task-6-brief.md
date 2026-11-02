### Task 6：增加独立 CLI 入口

**Files:** 修改 `src/agentcodec/cli.py`；新增 `tests/test_agentwire_cli.py`。

**Interfaces:** 注册 opt-in `benchmark-agentwire`；只加载仓库内固定 `bench/agentwire/exchanges.v1.jsonl` 和合成 `dictionary.v1.json`，不接受用户提供的语料/词典路径；必填 `--output-dir`（须是新的/空目录），`--tokenizers` 只接受仓库现有本地 profiles，`--instance-uses` 和 `--corpus-passes` 分别接受 1/10/50 组成的逗号列表，`--modes` 只接受 `gateway,model_direct`。缺省两类复用各跑 1/10/50，两种模式均跑；AC2/AWP1 候选仅因显式调用这个新 benchmark 命令才出现，不改旧命令或 encoder 默认。

- [ ] **Step 1：写失败测试。** `test_benchmark_agentwire_writes_versioned_report` 断言退出码 0、输出仅含 versioned redacted report 且条件/模式齐全；`test_cli_import_has_no_tokenizer_or_network_side_effect` 验证冷导入新旧 CLI 不初始化 tokenizer、不访问网络或用户数据；`test_cli_uses_only_fixed_synthetic_fixtures` 验证命令无 `--input` 或 `--dictionary` 路径参数且 fixture manifest identity 固定；`test_reuse_options_remain_separate` 验证 `--instance-uses` 与 `--corpus-passes` 独立解析 1/10/50；`test_unknown_options_and_invalid_reuse_fail_closed` 验证未声明参数、非正数和越界次数拒绝且无成功报告；`test_output_path_must_be_new_empty_directory` 验证现存非空目录、路径碰撞、symlink/special output 失败且不覆盖；`test_missing_local_tokenizer_never_downloads` mock 本地 meter 不可用并断言 report 为 cannot_compare、无网络调用。
- [ ] **Step 2：验证 RED。** 运行 `.venv/bin/python -m pytest tests/test_agentwire_cli.py -q`，确认新命令不存在或业务断言失败。
- [ ] **Step 3：实现入口。** 仅在现有 Typer app 注册新 command；无外部输入路径选项，只从固定版本化合成 fixtures 读取；沿用 UTF-8、重复 JSON key 检查和既有错误风格。输出写入新空目录且只含脱敏摘要/计量值，不回显消息、词典、payload、glossary、绝对路径或输入 hash；禁止自动安装/下载或连接模型。
- [ ] **Step 4：验证 GREEN。** 重跑同一命令，检查新 command help、复用单位分离和新目录输出；本地 tokenizer 不可用时保持明确 cannot_compare，不伪造指标。
- [ ] **Step 5：自审并提交。** 仅提交 CLI 与新测试，提交信息 `feat: add AgentWire benchmark CLI`。

