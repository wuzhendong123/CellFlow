
## /healthz 泄漏元数据库连接，控制台列表 500
**场景**: 服务跑几小时后方案列表为空、接口 500（`QueuePool limit of size 5 overflow 10 reached`）
**结论**: 健康检查用 `get_engine().connect().execute(...)` 没有关闭连接，容器每 30 秒检查一次，连接池被占满；改成 `with ... connect()` 归还
**来源**: 2026-09-29 / backend/cellflow/api/main.py

## 目标表绑定抽屉里下拉框显示为空
**场景**: 选完目标表后表名消失，下面列映射还在，校验提示「分区字段需要映射」
**结论**: `setB` 基于渲染时的旧 `b`，`await` 之后的第二次 `setB` 把 `table` 覆盖回空；异步取完数据后合并成一次 `setB`
**来源**: 2026-09-29 / frontend/src/pages/workspace/NodeConfig.tsx

## 分区替换的方案不适用「只执行最新任务」(D12)
**场景**: 同一方案连续提交多个文件（每个文件一批，如每天一份）
**结论**: 分区方案每个任务都执行（仍按提交顺序串行）；整表替换才作废排队任务。判断依据是方案版本里有无 strategy=PARTITION 的 SINK
**来源**: 2026-09-29 / backend/cellflow/services/jobs.py、runtime/executor.py
