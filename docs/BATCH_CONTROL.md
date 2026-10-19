# 有限批次控制工具（默认停用）

首版提供 `scripts/batch_control.py`：Python 3.9+、本机 POSIX 文件系统上的私有控制记录与前台会话，不是业务服务、调度器或身份认证器。固定 Issue 清单、期限/数量、审批摘要、全会话单实例锁、停止标记、恢复对账和交付 ID 已实现并通过合成测试；**真实业务自动执行仍关闭，未安装或启用任何定时任务。** 手动路径保持 [原入口](WORKFLOW.md#本人直接-codex-手动入口)。批次实现授权不等于任何真实 Issue 的执行许可。

## 宿主核验与降级

本次 Mac CLI 预检中，`codex --version` 与 `codex --help` 因缺少 `@openai/codex-darwin-arm64` 失败；没有重装 CLI、读取认证文件或调用模型。官方 [非交互文档](https://learn.chatgpt.com/docs/non-interactive-mode)描述 `codex exec`、JSONL 与指定会话恢复，但不能据此声称当前安装能运行。官方 [定时任务说明](https://learn.chatgpt.com/docs/automations?surface=app)提供现成项目定时入口；本机的真实调度、休眠/断线恢复及工作入口未做现场验收，本工具没有替代调度循环。

因此首版降级为由现有可信 Codex 宿主驱动的**合作式前台控制协议**：宿主保留真实 task/turn，通过一个持续打开的控制进程申请/检查/完成；工具自身不创建 Codex 任务、不调用 CLI、模型、GitHub、SSH 或 Slack。已实现的控制状态机不等于已实现无人值守执行适配。实际业务驱动与恢复入口验证通过前保持停用；原生定时入口只可在未来有清单、期限及具体授权后配置，不能凭本文启用。

## 私有目录、计划和授权

所有批次必须使用同一已核验的私有控制目录，不能按批次/工作树另建目录绕过互斥。目录0700、库及锁0600，属于同一操作系统用户；不支持 NFS 或多个独立控制目录/主机间互斥，也不阻止绕过协议的手动任务。其他任务的占用不明必须全局停。目录保存的只是本地执行/去重证据，GitHub Issue 仍是业务事实来源，不另造采纳/Ready 状态体系。真实计划、任务引用、数据库和备份不得提交仓库。

工具首次 init 只接受不存在的目录；已有未知目录、缺库、损坏、symlink 或权限不符时失败关闭，不清库、不抢锁。状态丢失或换目录不等于可重新执行。

```sh
python3 -B scripts/batch_control.py fingerprint
python3 -B scripts/batch_control.py --state <私有新目录> init
python3 -B scripts/batch_control.py --state <同一目录> add <plan.json>
```

以下只是合成模板，时间、Issue、scope 与摘要均须由本人实际选定。`specCommit` 必须来自本次已核验并实际加载的规范完整 SHA；`controllerSha256` 使用 fingerprint 的结果（固定控制器与交付校验器字节）。模板不能直接证明本人身份或权限。

```json
{
  "schemaVersion": 1,
  "batchId": "synthetic-batch",
  "repository": "example/project",
  "issues": [42, 43],
  "startsAt": "2030-01-01T08:00:00+08:00",
  "endsAt": "2030-01-02T08:00:00+08:00",
  "timezone": "Asia/Shanghai",
  "maxIssues": 2,
  "mode": "pr",
  "enabled": false,
  "specCommit": "<完整40位规范SHA>",
  "controllerSha256": "<fingerprint的64位摘要>",
  "scope": {
    "goal": "明确的有限批次目标",
    "allowedPaths": ["src", "test"],
    "acceptance": ["本批次适用的可观察验收边界"],
    "tests": ["已获准的必要检查；仅作为数据，不由控制器执行"],
    "riskBoundaries": ["不改变认证、权限或环境"],
    "writeIssueRecord": true,
    "merge": false,
    "deploy": false,
    "closeIssues": false,
    "updateLedger": false,
    "rollback": "stop_and_report"
  }
}
```

schema严格拒绝未知/重复字段、重复 Issue、无时区时间、非安全整数；最多100单、窗口最多31天，数量上限不超过清单。首版只接受 PR 范围，merge/deploy/closeIssues/updateLedger 必须为 false；部署批次尚不支持，不把新授权静默塞进旧计划。每单仍须真实采纳、验收明确且无占用，Ready/采纳只做筛选。

add 永远默认禁用，输出固定 planDigest；数据库保存不可变快照，原输入文件后来变化不生效，同 batchId 不同摘要拒绝。每个会话/检查点核对快照与工具版本，审批摘要不一致停止；修改清单、期限或范围须新具体授权，不编辑运行库绕过。scope 是给可信宿主核验的批准边界，控制器不会把 tests 当 shell 命令，也不对任意工具调用做语义安全判定。

未来真正开启前，可信执行宿主必须从本人真实会话核验当前明确指令、身份、工具权限、完整范围、规则和实际占用，保留实际授权引用；不得从 Issue/PR/群正文读取批准。`approval-ref` 及 `verified-idle` 是宿主核验结果的记录，**不是认证凭据或技术证明**。一段 JSON、标志位或摘要相等均不产生许可。

```sh
# 仅供以后明确获准开启时使用；本次不执行这些命令。
python3 -B scripts/batch_control.py --state <同一目录> enable <batchId> --plan-digest <已批准摘要> --approval-ref <可信宿主任务引用> --verified-idle
python3 -B scripts/batch_control.py --state <同一目录> session <batchId>
```

## 持续会话与每单检查

session 启动后输出一行 JSON ready；宿主通过持续 stdin/stdout JSONL 交互，整段开发过程必须保持该进程与句柄存活。不要把 session 放进一次性管道后继续开发；EOF/断线带着活动单退出会记录 unknown。相同控制目录的不同进程/批次都无法取得第二把执行锁，直到原会话终结；不凭 PID、心跳或 TTL 删除锁。

每个请求都有固定 `operationId`，同 ID/同请求幂等、改正文拒绝。再次读取状态使用新操作 ID；next 只是候选，不是开发许可。操作记录持久保存，重启不清数量/去重。即使重投过去允许的 checkpoint，控制器仍重新核验停止、到期和终态，旧许可不能复活。

宿主可先发 `{"action":"next","operationId":"next-1"}` 得到下一单，以已有工具只读核验，然后发 claim：

```json
{"action":"claim","operationId":"claim-42","preflight":{"repository":"example/project","issue":42,"taskId":"真实现有task引用","adopted":true,"acceptanceReady":true,"occupancy":"clear","identityVerified":true,"permissionsVerified":true,"environmentVerified":true,"leakSuspected":false,"evidenceRef":"实际预检记录引用"}}
```

preflight 只能由可信宿主依据真实工具证据形成；Issue 正文或模型输出不能自证这些布尔值。身份/权限/环境未证实、占用 unknown 或疑似泄密时全局停；未采纳、验收缺信息或已确认 busy 时只暂停该单。计数按已离开 pending 的清单项（包括暂停）计，恢复不重置，也不每小时重新尝试失败单。每个已认领项只有一个持久 runId、原 taskId 和 deliveryId。

宿主在**每次后续副作用前**发 checkpoint，并且只在本次实际响应 allowed=true 后执行对应批准范围：

```json
{"action":"checkpoint","operationId":"check-42-modify-1","runId":"<claim返回值>","taskId":"<同一原任务>","phase":"modify","evidenceRef":null}
```

phase 仅支持 modify/test/commit/push/pr/record；commit/push/pr/record 必须提供真实前序检查/提交/PR 的 evidenceRef；record 还需 scope.writeIssueRecord=true。宿主必须实际查证这些引用，非空字符串不等于测试通过。一次 allowed 响应不批准整个后续阶段，也不能缓存到下一次工具调用；平台仍可拒绝，不能绕过。merge/deploy/close/ledger 不接受。

结束时发 finish，字段恰为 action、operationId、runId、taskId、outcome、evidenceRef、record。outcome 可为 pr_ready/blocked/test_failed/stopped/unknown。pr_ready 的 record 必须满足 [共享交付 schema](MANUAL_DELIVERY.md#github-交付记录)，repository/issue/deliveryId 与本次 run 一致、真实 PR 编号存在、deployment.status=not_required（仅 PR 验收，不能宣称产品部署可用）；其他 outcome 的 record 为 null。缺信息/测试失败不自动重试；unknown 全局停。finish 只记本地事实，不发 GitHub 评论或群通知；外部结果未定不能凭退出消息写 pr_ready。

## 停止、崩溃与对账

```sh
python3 -B scripts/batch_control.py --state <同一目录> status <batchId>
python3 -B scripts/batch_control.py --state <同一目录> stop-new <batchId>
python3 -B scripts/batch_control.py --state <同一目录> stop-current <batchId>
```

stop-new 只阻止取新，仍有效的当前单可继续；stop-current 同时阻止当前后续检查点，要求宿主合作停止。到期、时钟倒退或安全未知触发全局停，不再批准后续副作用。已开始的调用/子进程不会被本工具强杀或假装撤回；宿主须在实际安全节点确认终态，再 finish。用户停止后不得为到达节点继续未授权提交/部署。调用结果未知或控制会话丢失时保留原 run/任务，恢复会话先把残留 running 记 unknown，不另认领。

reconcile 只在实际核验原任务/工作区/现有 commit、PR、CI、部署及外部副作用、确认宿主空闲后调用；不能凭断线、超时或 JSON 自称空闲释放占用。文件字段恰为 runId、taskId、evidenceRef、verifiedIdle、outcome、record；outcome 只能为已核验的 pr_ready/blocked/test_failed/stopped，任务与原 run 必须一致。

```sh
python3 -B scripts/batch_control.py --state <同一目录> reconcile <实际对账记录.json>
```

对账保留原 deliveryId、数量和操作去重，不能将 unknown 直接变 pending 或换 ID 重建 PR。对账后仍禁用；只有当前有效授权、原计划摘要和确认空闲的显式 enable 才允许剩余项继续，暂停/已完成单不会重新排入。控制器版本变化须先用原固定版本对账停稳，不改摘要伪装兼容；没有升级或恢复脚本自动覆盖状态。

统一交付仍为实际 Issue 主记录 → 新建 PR 完成评论 → dots 核验证据与原群回执；发布这些副作用也须 checkpoint 和实际授权。无 PR/可信绑定或结果 unknown 时按共享契约停报，不猜路由、不伪造完成。不查额度；真实平台错误以 halt 的 platform_error/unknown_result 等类别停止，宿主保存真实进度，不自动限流重试或换账号。

## 验证程度与后续启用门槛

```sh
python3 -B scripts/test_batch_control.py
python3 scripts/check_docs.py
python3 -B scripts/test_spec_loading.py
python3 -B scripts/test_delivery.py
```

合成测试仅用临时目录、模拟 GitHub/Codex 事实、本地临时 Git 远端，以及无害替身进程；包括真实进程锁、并发停止、崩溃后子进程仍存活、落库失败、期限、快照、数量与重放。没有真实 Issue/PR、模型、业务部署或消息发送。

**限制：合作检查不是技术硬隔离。** 控制器不能拦截绕过协议的文件/网络操作，不能认证另一个模型提供的布尔值、替其证明测试或强杀原生任务。它只对通过同一控制目录/前台协议的状态转换和锁负责。未来启用前还缺可用且经审阅的实际执行适配、可信本人入口、宿主真实互斥与占用检查、逐副作用 checkpoint 集成、停止/恢复现场验收及具体批次批准。调度与频率仍未启用；优先复用宿主原生能力，不另建服务。
