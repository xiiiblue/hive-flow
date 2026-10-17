# 手动执行方案与请求（本地组件阶段）

此版本提供独立、不可变的执行方案与本人请求入口。生产执行器、可信额度读取与派发处理器尚未连接；生产入口保持默认 `not_ready`。可信调用者可在完成执行链路配置与核验后，通过应用构造函数的 `manualExecutionEnabled: true` 选项启用手动请求协议；部署者也可显式设置 `HIVEFLOW_MANUAL_EXECUTION_ENABLED=true`；仅接受精确 `true` 或 `false`，省略时为 false。Issue 或聊天 JSON 不能设置该选项。这个开关仅启用协议，不证明外部执行链路就绪。安装此代码不会启用自动扫描、启动 Codex、修改源码、推送、开 PR 或部署。`/review` 继续只记录决定，永远不是执行许可。

## 执行方案

配置仓库的可信 Slack 响应方可发送一个新的根消息，精确前缀 `[wecom:execution-plan]` 后为单个 JSON 对象（总计最多 16384 UTF-8 字节）：

```json
{"repo":"example/project","issue_id":"9001","issue_number":12,"version":1,"base_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","files":["README.md"],"operations":["edit-code","run-tests"],"summary":"Update installation instructions","expires":1800000000000}
```

上例均为合成数据；有效期仅示意，提交时须在可信服务时钟之后、七天之内。

- 不接受额外字段、自由目标、频道或接收人。身份认证和频道校验由通用桥接完成，HiveFlow 执行业务校验。
- `repo` 必须等于配置仓库。`issue_id` 为 1–20 位正十进制字符串；`issue_number`、`version` 是正安全整数。Issue ID 与编号在执行方案和记录型提案之间双向绑定。
- 执行方案版本独立于记录型提案的 `revision`，无需预先创建或批准记录型评审。同一 Issue 的方案版本严格递增；重复、同版本冲突和旧版本均拒绝。
- `base_sha` 是 40 位小写十六进制完整提交标识。这里只保存身份约束，不访问 GitHub，不声称提交存在或为最新版本。未来执行器必须重新核验仓库、Issue 和提交状态。
- `files` 为 1–30 个唯一的精确相对文件路径，单条最多 300 UTF-8 字节；仅接受 ASCII 字母、数字、下划线、连字符、点和目录分隔符。拒绝绝对路径、空路径分段、`.`、`..`、`.git`、`.ssh`、`.aws`（忽略大小写）、`.env` 和 `.env.*` 凭据路径、通配符及反斜线。`.env.example`、`.env.sample`、`.env.template` 模板允许。路径表不是沙箱；未来适配器须验证符号链接、真实路径和所有实际改动。
- 操作仅为 `edit-code` 和 `run-tests`，不得重复。没有 push、merge、open-draft-pr 或 deploy 权限。
- `summary` 为非空单行，最多 2000 UTF-8 字节，不接受控制字符。`expires` 是正安全整数 Unix 毫秒时间，必须在未来且最多七天。

HiveFlow 产生唯一方案 UUID，保存完整原始字段快照与固定本人、频道和根线程。新版本不覆盖旧快照；旧版本未发出的预览失效，历史请求结果保留。启用手动协议时，新版本还原子撤销旧版本执行许可，并对旧版本在途尝试设置检查点停止。未就绪模式预览发送本人，并明确“执行器尚未就绪，不收集执行批准”；不会显示可复制的执行命令。所有显示文本均作为转义数据处理。

## 本人请求与失败关闭

协议预留精确命令 `/execute #<issue-number> v<version> <plan-uuid>`，单个 ASCII 空格分隔。它只在已认证的本人企业微信单聊入口解析；仅提供编号、自然语言“执行”或普通 `/review` 都不能替代版本和 UUID。群消息仍遵循原传输规则，不进入执行入口。

默认未就绪模式收到有效命令时，在同一 SQLite 事务中保存一次终态 `not_ready / executor_unavailable` 请求，并向本人和方案原始 Slack 根线程分别写入结果发件箱。结果明确 `execution_authorized: false`、`execution_started: false`。没有执行授权、待派发队列或后台工作；重启、接入执行器、额度恢复均不会重放它。同一方案重复命令或同一来源事件不会重复创建请求。后续真正接入执行能力，须发布新版本方案并重新取得明确执行请求。

错误身份、版本、仓库、过期或已替代方案、格式错误均拒绝，不猜测任务，不回落成普通私聊新任务。业务记录不受普通消息 TTL 清理影响；通知有独立 TTL，记录存在不证明送达。发送不确定时沿用桥接 outbox 的 unknown 状态，不自动重试，防止重复副作用。

Slack 结果第一行精确为 `Execution request result`，第二行为 JSON；metadata `event_type` 为 `hiveflow_execution_request_result`。字段：`request_id`（业务请求 UUID）、`plan_id`、`repo`、`issue_id`、`issue_number`、`version`、`status`、`reason`、`execution_authorized`、`execution_started`。结果接收方必须核验实际 App 作者、固定频道和原方案根线程，不能只相信前缀。

## 可信就绪路径（默认不启用）

`new IssueReviewApplication(config, { manualExecutionEnabled: true })` 或 `createIssueReviewApplication(env, options)` 是可信应用集成选项，不是给消息发送方的权限开关。当前 `src/main.js` 从严格解析的 `HIVEFLOW_MANUAL_EXECUTION_ENABLED` 读取这个选项，默认关闭；未修改任何生产环境配置。集成者必须先核验真实手动处理器、运行环境及额度读取能力，不能为了显示命令而设置为 true。

开启后，预览会提供精确可复制的 `/execute` 命令。本人发送后，HiveFlow 在同一事务中保存独立的执行许可、`requested` 请求与 Slack 原方案线程的触发事件。事件只表示“已记录本人明确的请求，尚未启动”；不会调用任何网络执行器。许可绑定本人、唯一方案 UUID、仓库、Issue 身份、版本、基准 SHA、精确文件集合、操作和到期时间；`/review` 不参与这条路径。

触发消息第一行为 `Execution requested`，第二行为单个 JSON，metadata 类型 `hiveflow_execution_requested`。除前述共同字段外，包含 `grant_id` 和 `plan`（完整方案快照加 `id`）；`status: requested`、`reason: explicit_owner_request`、`execution_authorized: true`、`execution_started: false`。`grant_id` 等于业务 `request_id`。消费者须核验实际桥接 App 作者、固定私有频道、方案原根线程及权威 Store 中的许可状态，不能仅凭 JSON 创建任务。旧版本撤销、到期、配置改变或重启为默认未就绪时，尚未送出的触发事件不能再发出。

重复请求不会重发触发事件。通知未知或过期不能证明任务未创建，也不能据此自动再派发。纯 `ManualDispatch` 状态组件将 `reserve → startDispatch → recordStarted → checkpoint → confirmTerminal` 显式分开：先占单任务槽、再在可信新鲜额度严格大于 5% 时消费许可，持久化 `dispatch_started` 后才能尝试外部任务创建；创建结果不明须保持占用并人工核验。测试只使用合成任务 ID，不证明真实执行器已接通。

控制结果只证明状态组件记录了该步骤，不是测试通过或代码完成的证据。外部父会话须核验实际子任务结果、测试和文件范围后，才能提交 terminal；最终用户摘要可沿用原方案根线程的 `[wecom:owner]` 路由。当前模块不自动生成 GitHub 评论、提交、PR、群通知或部署结果。

可信响应方的新根控制消息、原方案线程确认及状态操作格式见 [手动派发控制协议](EXECUTION_CONTROL.md)。

## 后续接线边界

纯状态模块不等于已配置的执行系统。可信手动请求路径已复用共享 Store 事务，将本人明确命令、独立执行许可和通知原子提交；后续外部派发处理器必须继续遵守状态协议；不能从记录型决定推断许可。单任务认领、真实新鲜额度大于 5%、执行前检查、源提交和文件范围、停止与崩溃恢复均须验证。父会话到现有 Codex 环境的实际连接也须单独配置和验收。不得添加通过聊天内容设置的 ready 标记，不得在 PVE 新增绕过现有连接的执行 API。
