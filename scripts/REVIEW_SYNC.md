# 修订同步部署与验证

## 部署

1. 先执行 `scripts/migrations/0022_add_review_sync.sql`。脚本可重复执行，新增开关默认关闭，历史修订不关联。运行时结构补齐也包含相同迁移。
2. 部署后端及现有 segment-sync worker，再部署前端。失焦请求唤醒现有 worker；API 进程每 15 秒补偿扫描持久化修订任务，进程重启后可继续消费。
3. 管理员进入项目设置的“自动应用与锁定”，开启“修订同步”。原有片段级同步禁用仍有效，旧的失焦白名单不限制修订同步。
4. 开启工作台修订模式，修改一处重复译文并离开编辑框；符合条件的同项目、同语言对片段出现关联修订。接受／拒绝任意关联修订会联动处理未独立修改的成员。

修改前已有独立待处理修订的片段不会自动并组。需先处理该修订，再开始新的一轮编辑。关闭项目开关停止后续传播，已有组仍可接受／拒绝。

## 接口兼容

- 项目 PATCH 增加可选布尔值 `review_sync_enabled`。
- 片段响应增加 `review_sync_enabled`、`review_sync_group_id`；后者仅对待处理组的源片段返回。
- 原失焦 POST 增加 `mode: review`、`group_id`，保留 `expected_version`；响应增加 `task_id`。省略 mode 沿用翻译同步，但活动修订组不会进入旧同步通道。
- `GET /api/review-sync/tasks/{task_id}` 返回状态、代次及结果，仅组作者可查询。相同失焦 POST 可重试失败任务。
- 修订响应增加组标识、来源片段及关联数量；接受／拒绝响应增加 `review_sync_result`。批量接口沿用 `updated_count`，新增相同汇总；数量含跨文件联动结果。

## 验证

后端测试须使用专用 PostgreSQL 测试实例，并显式设置 `REVIEW_SYNC_TEST_DATABASE_URL`。测试创建随机 schema，结束时仅删除自身 schema；需要创建 schema 及测试扩展的权限。

```powershell
$env:REVIEW_SYNC_TEST_DATABASE_URL = 'postgresql+psycopg://test_user@127.0.0.1:55439/postgres'
.venv/Scripts/python.exe -m unittest discover -s scripts -p test_review_sync.py -v
```

前端：在 frontend 目录运行 `npx playwright test --config playwright.review-sync.config.ts` 和 `npm run build`。浏览器测试使用模拟接口验证真实 Pinia store 的保存顺序、单文件／合并视图修订刷新、联动拒绝和失败重试。

## 运维

`review_sync_tasks` 保存每组最新任务及结果。失败重试最多 3 次；重试中的任务保持 pending，3 次失败后置 failed，可从工作台重试。任务与片段更新同事务提交；不使用脱离事务的 processing 标记，进程崩溃后数据库回滚即可恢复。

日志记录组、代次、更新／跳过数量及失败。检查 pending 任务的 updated_at 可判断积压；跳过原因区分旧译文不同、独立修订、无权限、禁用和源版本失效。项目事务锁统一保存、传播与联动操作的锁顺序；大规模重复组可能增加该项目保存等待时间。

回退行为时先关闭项目开关并停用新消费者，保留新增表以保留修订关系；不要通过删表清理仍待处理的关联修订。
