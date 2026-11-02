# Task 2 报告：AWP1 严格编解码

## 状态

已完成并本地提交。分支：`agentcodec-agent-communication-design`。基线：`1c329e2cfaaef30bc9b13226c0e90b5f92ca176e`。提交：`cb0bc6f07132564c511a3113e7966ac5123dbd9f`（`feat: add strict AWP1 codec`）。

## 实现范围

只新增了 brief 指定的两个实现文件：`src/agentcodec/agentwire/codec.py` 与 `tests/test_agentwire_codec.py`。编码器输出固定 `AWP1|[identity,message_values]` 二元数组；身份只接受 null 或经过严格校验的三项数组；消息按 MessageV1 的 11 个固定位置编码。消息类型与状态使用各自独立、固定的短码表。

解码器在 JSON 解析前以真实 UTF-8 字节数执行接收方大小限制；解析器拒绝重复对象键、JSON 非有限数字、未知 profile、坏 envelope、身份/hash 格式错误及 lone surrogate。所有字段解码后统一 strict 验证 `MessageV1`，只有完整通过才返回；失败统一抛出不回显输入的固定 `AgentWireError`。还对深嵌套 JSON 增加了迭代式 Unicode 检查与递归溢出稳定拒绝。

显式选择词典时，编码仅在五个许可文本字段的完整字符串或完整列表项上将 phrase 替换为 `{"$ref":"code"}`；消息 user/version、wire identity、所选词典和接收端身份必须精确匹配。ID、枚举、布尔值、字段名及子串不替换。无显式词典时不扩展/产生引用，并保留原 dictionary_version。编码/解码未改动输入消息或词典。`negotiate_awp1` 直接复用 decoder 的严格路径，只返回 `NegotiationResult`，不接收外部解析消息、身份或字节数。

## 验证

新增测试均使用代码内手写的合成消息、固定 wire golden 和手写词典。先观察到 10 项测试因 codec 模块/API 缺失而 RED；期间修正了测试夹具中的错误字典 hash、枚举位置及有效 dictionary metadata。行为测试还捕获了编码端 phrase→code 方向错误，并以独立 RED 测试发现深层 JSON 的递归扫描异常；修正后定向测试通过。

最终命令与结果：

- `/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest tests/test_agentwire_codec.py -q`：11 passed。
- `/workspace/agentcodec/.worktrees/agentcodec-mvp/.venv/bin/python -m pytest -q`：1672 passed in 194.67s。
- 两个新增 Python 文件的 `py_compile`：通过；`git diff --check`：通过。

未安装或下载依赖，未调用真实模型/provider，未读取、复制或提交原始语料/词典内容。未修改 Task 1、AC1/AC2、旧 decoder 或其他产品文件。提交只含 brief 指定的两个文件；既有未跟踪计划文档未暂存、未修改。

## 审查与待办

已完成作者自审。按本次 dispatch 的明确要求，没有派发实现子代理或独立 reviewer；独立复核由协调者在接收报告后处理。未发现已知未解决的技术问题。合并/推送未执行。
