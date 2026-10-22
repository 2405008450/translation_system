# 可信译文自动修改保护

保护与流程确认状态独立。采用非空 TM 100% 匹配译文时自动保护；人工编辑、取消确认和阶段流转保留保护。所有自动翻译、校对、重匹配、同步接收和 AI 建议应用均跳过受保护译文。人工可直接编辑，或明确解除保护后重新执行自动处理。

## 发布顺序

1. 备份数据库，先执行 `scripts/migrations/0030_add_auto_write_protection.sql`。
2. 发布后端并重启应用、预翻译和维护 worker，再发布前端。运行时 schema 检查也识别这些字段。
3. 核验工作台保护标识、单句开关和“保护选中／解除选中保护”；版本冲突时刷新后重试。
4. 新增字段不改变历史译文。历史保护使用下面的预览和核验工具单独回填。

不要在应用或 worker 混用新旧版本期间启动自动写入任务。旧版本不了解保护字段，无法保证保护规则。

## 历史数据预览与回填

在项目根目录、配置好目标数据库连接的环境中执行。命令默认只读；输出 UTF-8 JSONL，可逐条检查 `eligible`、`reason`、版本、TM 记录 ID 和更新时间。

```powershell
.\.venv\Scripts\python.exe scripts/backfill_auto_write_protection.py --project-id <项目UUID> --limit 1000 --output protection-preview.jsonl
```

下一批预览添加 `--after-id <上批输出的next_after_id>`。只有当前来源为 TM、分数为 1、匹配原文证据完整、所选库及语言对内唯一记录的原文/译文/更新时间均核验一致，才允许回填。证据缺失或有歧义时不推断。

检查预览后，按清单执行：

```powershell
.\.venv\Scripts\python.exe scripts/backfill_auto_write_protection.py --project-id <项目UUID> --apply --manifest protection-preview.jsonl --output protection-applied.jsonl
```

执行时重新核验当前版本和 TM 证据，锁定句段后修改保护状态，不改译文、格式或确认状态。可重复执行；已保护或人工解除的句段不会被历史回填再次修改。每批最多处理 `--limit` 条清单候选。

## 接口与上下文

`POST /api/files/{file_id}/segments/auto-write-protection`

```json
{
  "state": "protected",
  "items": [{ "segment_id": "句段UUID", "expected_version": 3 }]
}
```

解除使用 `state: "released"`。接口沿用文件及句段写权限、文件操作锁；单批最多 1000 项，逐项返回权限、缺失或版本冲突。成功变更递增版本并记录操作者。重复 ID 只处理一次。

校对按句段 ID 分别生成结果，只接受本次待校对 ID。只读参考取同文件、同语言和同列绑定的前后各两条，限制在当前用户可读区间。参考原文、译文分别最多 1000 字符，总参考预算 6000 字符，参与 24000 字符分包估算；距离较远的参考优先省略。正在校对的相邻项只通过邻接关系引用，不重复标记为只读项。

日志记录 `automatic write skipped`、`automatic write protection changed` 及 `proofreading context`。任务响应的 `skipped_protected_segments` 与失败数分别统计。


## 验证记录

- 相关后端回归 285 项、22 个子测试通过；后续新增的 AI 格式保护测试也已通过。
- 工作流浏览器回归 29 项通过，新增批量保护及版本冲突场景通过；已检查工作台截图布局。
- 前端类型检查、生产构建、Python 编译及差异空白检查通过。
- 扩展运行拼写/术语 QA 测试时，有 4 项因旧测试夹具缺少项目表已有的 `review_sync_enabled` 字段，在创建项目时失败。这些夹具问题未通过修改业务规则绕过。
- 本次未主动执行真实数据库迁移或历史回填，也未调用真实 LLM。
