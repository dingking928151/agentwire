### Task 2：实现 AWP1 严格编解码

**Files:** 新增 `agentwire/codec.py`、`tests/test_agentwire_codec.py`。

**Interfaces:** 消费 Task 1 合同；实现 `encode_awp1(message, dictionary=None) -> str`、`decode_awp1(payload, receiver, dictionary=None) -> MessageV1`、`negotiate_awp1(receiver, payload, dictionary=None) -> NegotiationResult`。协商与 decoder 共用同一 strict parser，不能接受调用者伪造的已解析消息、identity 或 byte count。

- [ ] **Step 1：写失败测试。** 手写独立 11 字段 fixture 与固定 wire golden。`dictionary_identity_or_null` 必须为 `null` 或 `[user_id,version,sha256]` 三项数组；拒绝自由字段对象、错长度和非小写/非 64 hex hash。`test_awp1_golden_round_trip_preserves_all_eleven_fields` 断言全字段往返严格相同；`test_dictionary_identity_and_whole_value_refs_round_trip` 验证仅五个批准文本字段的完整字符串/列表项可替换。`test_decoder_enforces_actual_utf8_byte_limit_before_json_parse` 以多字节 Unicode 验证 parse 前按实际 bytes 拒绝超限；`test_bad_envelope_or_any_bad_field_fails_closed` 覆盖错前缀、重复 object key、JSON 非有限值、top-level/二元 envelope/11 项数组与 identity 结构错误、未知枚举（包括两表同码不串表）、错类型/身份/hash/namespace/version、缺失/未知引用和 lone surrogate，失败没有 payload。`test_dictionary_version_mismatch_rejects_without_metadata_rewrite` 验证 codec 不替 transport message 改 metadata；`test_negotiation_reports_wire_or_receiver_mismatch_without_payload` 覆盖不支持 profile/schema/enum、receiver user mismatch、字典缺失/身份冲突、超限和坏消息；`test_negotiation_uses_wire_identity_and_actual_size_only` 验证不接受调用方伪造解析内容、身份或 byte count；`test_unselected_dictionary_version_is_preserved_without_alias_expansion` 验证没有显式 dictionary 时保留已有版本且不展开引用。
- [ ] **Step 2：验证 RED。** 运行 `.venv/bin/python -m pytest tests/test_agentwire_codec.py -q`，确认目标接口/断言失败。
- [ ] **Step 3：实现最小 codec。** Wire 固定为 `AWP1|` 加 `[dictionary_identity_or_null, message_values]`；身份为 null 或 `[user_id,version,sha256]`。`message_values` 按 spec 的 11 个字段顺序，JSON 字符串负责分隔符/引号/换行/Unicode。显式 dictionary 编码要求输入 transport MessageV1 的 `user_id` 和 `dictionary_version` 已与 dictionary 精确匹配；缺版本/冲突时拒绝，由单独的 transport helper 建立新副本，codec 不改输入或自动改 metadata。字典 identity 未选时不展开任何 `$ref`，即使消息保留非空 dictionary_version。完整 decode 后才严格 `MessageV1.model_validate(..., strict=True)`；既有 AC codec 不改。能力上限始终由 decoder 自己以实际 payload UTF-8 长度执行。
- [ ] **Step 4：验证 GREEN 与边界。** 重跑同一命令；增加 null/false/空串/空列表、列表顺序/重复、尾空格、`|`/逗号/括号/换行、Unicode 分隔符、对象非精确 `$ref` 形状、null identity 下出现 ref 和字典输入不被变更等测试。
- [ ] **Step 5：自审并提交。** 仅提交 codec 与测试文件，提交信息 `feat: add strict AWP1 codec`；确认任何未知值都整体拒绝，无半解码返回。

