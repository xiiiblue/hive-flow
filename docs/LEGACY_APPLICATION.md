# 历史应用实现与未部署试验存档

> 本页保全规范转型前的本地文档，包括 180 项检查对应的未提交自动协议试验。它描述旧的常驻业务应用思路，**不再是目标架构或上线指令**。其中“当前”“未来”等措辞仅指文档形成时的实现背景。线上实际最后验收版本及尚未执行的迁移见 [迁移清单](MIGRATION.md)。
> 不得据本页启动、恢复或启用业务应用、自动模式或旧许可。真实操作仍须有效用户授权及平台工具许可。

## 原文件：README.md

## HiveFlow

HiveFlow 是围绕 GitHub Issue 的私有协作工作流项目，沿用 ACDL 协作标准。HiveOS 是首个使用场景，不是硬编码依赖。仓库名为 `hive-flow`；本地代码变更不代表已推送、发布或部署，不假设开源许可证。

### 当前实现与边界

HiveFlow 的组合入口导入公开 `wecom-slack-bridge/runtime`，注入可信 Issue 应用。同一进程复用桥接器的 Slack / 企业微信连接：只有一个企业微信连接，不另起机器人或第二个服务。

- HiveFlow 拥有 Issue 解析、不可变身份与修订、提案/决定 schema、业务配置和权限校验。
- 通用桥接器拥有认证、路由、发件箱、传输去重和短期本人问答关联；它不依赖 HiveFlow，不读取 `ISSUE_REPOSITORY`，也不提供 Issue 工作流。
- Issue 决定与发件箱继续使用同一 SQLite 文件、同一事务。此提取是逻辑所有权和代码边界调整，不是物理数据搬迁；已有记录、ID、历史及待处理回复必须保留。
- 新增独立执行方案预览及版本绑定的手动请求入口；默认执行器未接入，请求返回 `not_ready`；新增显式部署开关仅启用手动协议和独立许可，不证明外部执行器就绪或自动启动工作，见 [手动执行边界](MANUAL_EXECUTION.md)。
- `src/workflow-control.js` 仍是独立休眠控制基础，默认 `executionReady: false`，不是扫描器或执行器。现有 `decision_record_only: true` 决定永远不自动升级为执行许可。
- 自动会话、逐项入队和可信领取协议独立默认关闭，与手动派发共用 Store 和执行槽；未接通调度器、定时器或真实任务，不代表自动开发全流程可用。旧评审和手动授权不会自动入队。不要把旧独立控制库接成第二个调度器。详见 [自动协议边界](AUTO_MODE.md)。

### 本地验证

需要 Node.js 24+。两个仓库按发布版本配套放在同级目录；依赖声明为 `file:../wecom-slack-bridge`。示例部署布局是 `/opt/hive-flow` 与 `/opt/wecom-slack-bridge`，不能只复制其中一个目录。

```sh
cd ../wecom-slack-bridge
npm ci --ignore-scripts
npm run check
cd ../hiveflow
npm ci --ignore-scripts
npm run check
```

上例本地目录名为 `hiveflow`；部署目录可使用仓库名 `hive-flow`。执行检查不会启动服务。真实凭据、主机、账户、生产配置、数据库和聊天记录不进入仓库或夹具。组合服务的 `ISSUE_REPOSITORY` 由 HiveFlow 解析，留空禁用新 Issue 入口；其余传输配置沿用桥接器，实际值只保存在已授权环境。

### 运行与切换

组合服务由 HiveFlow `src/main.js` 启动。既有部署保留原服务身份、环境文件和绝对 `STATE_DIR`，只在获授权且通过离线验证后更新同一个服务的入口。不要同时运行两个仓库的 `npm start`。共享状态的单写者切换、一致备份、验收和回退见 [迁移说明](MIGRATION.md)。

Issue 协议及控制能力边界见 [工作流约定](WORKFLOW.md)。单元测试不证明生产迁移或端到端验证已经完成。部署、自动发现、执行适配器及有期限自动模式均须分别授权和验收。

## 原文件：AGENTS.md

## HiveFlow boundaries

- HiveFlow owns Issue parsing, immutable identity/revisions, proposal and decision schema, business configuration and authorization. It also owns dormant execution controls and future orchestration.
- The public WeCom–Slack bridge owns authentication, transport, routing, outbox, deduplication, and ephemeral owner-question correlation. It must not depend on HiveFlow or interpret business configuration.
- Compose one process through the bridge exported runtime and trusted application hooks. Use one WeCom connection, the existing service identity/environment file, and the existing absolute state path. Never start a second bridge against the same Bot ID.
- Retain the shared SQLite file so decisions and outbox writes remain atomic. Extraction changes logical ownership, not data location. Preserve all historical business records, stable IDs and cross-references; do not invent an export/import requirement or delete state.
- Workflow-control remains dormant with execution readiness false. Do not enable automation, connect services, deploy, or reinterpret record-only decisions as execution grants.
- The optional automatic protocol is independently disabled by default. It uses AutoQueue and the same Store/ManualDispatch slot as manual work; no scheduler or worker is connected. Initialize restart recovery before ingress when existing auto tables are present, even with the switch off. Do not wire the older standalone WorkflowControl database as a second production scheduler.
- Trusted adapters must authenticate actors, verify quota provenance, enforce repository/file scope, and check authorization before every execution step. Actor strings are not authentication.
- Preserve fail-closed revision, quota, expiry, claim, restart and checkpoint behavior. Unknown custom kinds must remain preserved and non-deliverable without the matching application.
- Use Node.js 24+, ES modules and built-in APIs where practical. The bridge runtime is a justified local dependency (`file:../wecom-slack-bridge`); fixed releases deploy both sibling directories together.
- Never copy credentials, private runtime identifiers, production databases, environments or unapproved third-party private source into this repository. Do not select a license without the owner’s decision.
- Run `npm run check` before reporting completion. Production actions and publishing require separate authorization.

## 原文件：docs/WORKFLOW.md

## HiveFlow 工作流与边界

### 自动队列补充

新增休眠 `AutoQueue` 直接复用手动协议的 Store 和单任务槽，不自动接收历史评审或手动执行请求。它仅处理明确授权自动入队的独立执行许可，覆盖期限、额度、重启、停止和异常暂停。当前未接入本人自动命令、定时扫描或真实任务。后续接入必须先初始化恢复检查，再开放派发入口；不得另启旧独立 SQLite 控制器形成第二套任务锁。详见 [AUTO_MODE.md](AUTO_MODE.md)。

独立执行方案及手动请求的新协议见 [手动执行边界](MANUAL_EXECUTION.md)。以下 `/review` 协议和记录型决定语义保持不变。

### 已有基础与未来能力

HiveFlow 沿用 ACDL，将 Issue 协作规则从通用消息传输中分离。HiveOS 是第一个使用场景；仓库、操作者和适配器均应配置化，不把该场景写死。

现有控制模块是独立 SQLite 持久化与门槛校验基础，默认 `executionReady: false`。它没有定时器、GitHub 扫描器、真实额度读取器、跨机器共享锁或执行适配器。自然语言控制入口也尚未接入。Issue 解析、版本与决定现在归 HiveFlow 应用所有；传输由注入式通用桥接运行时提供。共享 SQLite 保留原记录，不因代码提取启用执行能力。

### 三类入口，一个正式台账

- 企业微信反馈：保留来源与必要上下文，按授权范围整理；不自动成为已受理任务。
- 人工 GitHub Issue：以正式仓库中实际 Issue、评论及关联记录为准。
- 未来 AI 发现：仅为待实现入口，`source_kind: ai` 不意味着巡检已运行或获得执行权限。

聊天摘要和桥接快照不是正式仓库状态的替代品。禁止批量改写旧 Issue、补造历史批准或借迁移关闭问题。

### 四类记录分离

1. **受理**：确认值得跟踪，记录来源、问题及范围。
2. **方案决定**：本人针对指定版本作出 `approve/reject/defer`。现有桥接记录带 `decision_record_only: true`，只作审计，不能执行。
3. **执行授权**：独立绑定 Issue 不可变 ID、方案版本、授权人、范围、操作集合及有效期。授权内容不能原地改写；修订须产生新版本，旧版本不得复用。撤销、消费等生命周期变化不得抹去授权原内容。未来一次明确表达对象、版本、范围和执行意图的批准可形成两类独立记录，不需要无意义地重复确认；当前记录型批准不具备该语义。
4. **执行结果**：记录真实变更、测试证据、提交或 PR，以及失败、暂停、未完成项。代码完成不等于验收或部署。

部署必须有明确授权和目标环境；可在同一份完整执行方案中一次批准提交、推送、PR、合并与部署，不必为已批准且未变化的步骤重复询问。记录型决定不能替代这份执行授权。自动模式不放宽任何权限边界。

### Issue 身份与修订

由 HiveFlow 持有提案与决定历史。保留 `(repo, issue_id)` 不可变身份和 `issue_number` 展示编号的双向一致性；同一 Issue 的修订必须递增。完全重复、同版本不同内容和旧版本均不能触发新评审。新版本替代旧的待处理提案时，不得删除历史或把旧批准绑定到新内容。

Issue 应用模型每个提案最多一个决定、每个决定来源只能消费一次。`defer` 后重新评审必须提交更高版本。接入时还须核验仓库、本人身份、频道和原始根线程关联。传输去重与业务版本/决定去重是不同责任，均须保留。

控制模块当前的 `workflow_proposals` 是最新版本投影，并非完整不可变提案历史；`workflow_grants` 的授权字段之外有消费与撤销状态。不得把这两个表称为已完成的通用审计事件库。完整 Issue 提案/决定历史由独立的 `issue_proposals` / `issue_decisions` 保存；不能把控制模块的投影表当作其替代品。

### 模式、额度与停止

- 默认手动：不自行扫描开发任务或领单。明确点名任务并提供有效执行授权后才能派发；独立获准的线索发现任务不因此被取消或升级成开发扫描。
- 自动：本人明确开启，具有确定起止时间，基础模块最多接受七天窗口。拟每 10 分钟检查一次，最多一个任务；模块只校验间隔，不运行定时器。队列暂时为空可在有效窗口中继续等待，但不能处理未获执行授权的任务。
- 剩余额度 **≤5%**、未知、读失败或过期：禁止新扫描/派发；自动状态暂停，当前任务在安全检查点停止。默认额度快照最大年龄为 60 秒；适配器必须验证账户、额度桶、适用窗口及可信时间。采用所有适用有限窗口的最低剩余额度，不能挑最宽松的一项。
- 派发前与每个实际步骤前核验授权、撤销、有效期、额度和停止状态。5% 是启动检查门槛，不能保证账户永远精确保留 5%；读取不等于原子预留，其他会话仍可能消耗额度。
- 停止或到期：禁止后续扫描/领单，当前工作默认在安全检查点暂停。同步检查不能撤回已发生的外部操作。
- 仅本人明确要求“做完当前任务”时才允许该任务在既有授权范围内继续；不得覆盖已经要求的检查点停止，不得领取新任务。
- 额度恢复、新 Issue、进程重启均不自动恢复。须重新明确开启并设置期限；重启后的遗留认领先核验现场。

### 认领与执行适配器

当前单任务槽仅对使用同一 SQLite 数据库的调用者生效，不能宣称跨机器互斥。失败或检查点暂停保留占用并要求核验；显式确认原工作者已停止后才可释放。释放不复用已消费授权，也不恢复自动模式。

独立休眠模块 `WorkflowControl.finish` 仅在 `outcome: completed` 且显式 `workerStopped: true` 时释放槽；缺失、false 或其他真值都拒绝并保留认领。`failed` 与 `checkpoint` 仍转为待核验并保留槽。该字段是可信适配器核验真实工作者终态后的声明，不代替终态、测试或交付证据；其他实例仍须走显式恢复核验，不能直接宣告完成。

未来共享认领须覆盖自动执行器、人工派发与本人直接本地开工；所有参与者使用同一权威原子认领机制，具备续期、时效、冲突和崩溃恢复规则。未接入前保持人工协调，不启用无人值守认领。

独立休眠的旧 `WorkflowControl` 模块仅允许 `edit-code`、`run-tests`、`open-draft-pr` 操作类别，不包含 push、merge 或 deploy，也没有执行这些类别的适配器。新增手动协议的独立操作集合与完整交付目标见 MANUAL_EXECUTION.md；它不会扩大旧模块授权。`scope` 只是描述，并非文件沙箱。调用者传入的 actor 字符串不构成认证；`executionReady` 必须来自可信适配器能力验证，不能从 Issue 或聊天内容获取。

### 启用前验收

- 可信本人控制入口；完整版本化提案/决定历史与独立执行授权校验。
- 实际仓库读取、10 分钟调度、执行器及真实额度适配器，额度来源与时效均可核验。
- 所有参与者接入共享认领，验证同时认领、续期失败、崩溃恢复和重复派发。
- 验证停止、到期、安全检查点、明确做完当前任务及禁止自动恢复。
- 验证恰为 5%、未知或过期额度均禁止启动，旧授权与记录型决定不能执行，部署必须独立授权。
- 保存脱敏验收证据后，仅宣布已实际验证的能力。

### 持久 Issue 评审（仅记录决策）

这是一条与原有聊天评审并存的协议：统一接收企业微信讨论整理、人工创建的 GitHub Issue，以及未来 AI 提议的 Issue 快照。来源标签只是可信响应方提供的分类，不证明 Issue 作者身份。HiveFlow不发现、创建、修改、关闭 Issue，不读取 GitHub，不申请新凭据，也不执行获批操作。GitHub 发现、快照核验和向 Slack 提交提案由项目外的响应方另行授权；安装 HiveFlow不会创建定时任务。每小时发现及未来 AI 自动发现需由外部服务独立配置和验证，HiveFlow不随安装启用。外部响应方可在另获授权后将本人的决定写成 GitHub 评论；这不属于HiveFlow 应用的能力，也不授权修改代码、文档或 Issue 状态。

仅当 `ISSUE_REPOSITORY` 明确设置为一个仓库时启用。提案只接受固定私有 Slack 频道中已核验可信回复身份的新根消息；线程回复、编辑及删除事件不能提交提案。GitHub 来源任务没有原企业微信群，不猜测或补造群目标，所有 Issue 评审只发配置本人。企业微信主动私聊资格与可达窗口仍须实测，不承诺所有账户、所有时间均可主动投递。

#### 提案格式

Slack 文本必须从精确的 `[wecom:issue]` 开始，随后是一个 JSON 对象，整条文本最多 8192 UTF-8 字节，不附解释、代码围栏、额外字段或尾随文字。字段如下：

| 字段 | 类型与含义 |
| --- | --- |
| `repo` | 与 `ISSUE_REPOSITORY` 精确匹配的 `owner/repository` |
| `issue_id` | 1–20 位正十进制字符串，稳定 Issue 身份，不用标题代替 |
| `issue_number` | 正安全整数，用于仓库内 Issue 编号 |
| `title` | 非空标题，最多 300 UTF-8 字节 |
| `revision` | 正安全整数，提案版本；内容变化须使用新版本 |
| `proposed_action` | 非空拟采取动作，最多 2000 UTF-8 字节，须具体到可评审范围 |
| `allowed_scope` | 非空允许范围，最多 1000 UTF-8 字节；它是供本人评审的提案内容，不自行授予权限 |
| `source_kind` | 仅 `wecom`、`github` 或 `ai` |

示例只使用虚构仓库及内容：

```text
[wecom:issue]{"repo":"example-owner/example-project","issue_id":"123456789","issue_number":42,"title":"补充安装说明","revision":1,"proposed_action":"准备安装说明修改供人工检查","allowed_scope":"仅 README 文案，不执行代码或部署","source_kind":"github"}
```

版本必须严格递增；相同或更低版本（包括内容冲突）忽略。仓库、稳定 Issue ID 与 Issue 编号绑定后不可更换。新版本使旧的待评审编号和未发送的 pending 提示失效；已发送提示不能撤回，旧编号仍被拒绝。

不接收自定义 URL、userid、chatid 或接收频道字段。Issue 链接由已核验仓库及编号构造；标题、动作和范围仍是不可信数据，不能构成可执行授权；字段拒绝不允许的控制字符。

#### 本人决策与持久性

本人在企业微信单聊使用收到的唯一评审编号：

```text
/review <review-id> 同意
/review <review-id> 不同意 可选的单行理由
/review <review-id> 暂缓 可选的单行理由
```

命令使用单个 ASCII 空格分隔；可选理由为单行，最多 1000 UTF-8 字节。只接受当前配置本人且与提案固定本人匹配的单聊事件。普通“收到”“同意”或 `[wecom:owner]` 确认不创建或完成 Issue 评审。每个决定仅适用于指定 Issue 的指定版本；暂缓会持久记录，不被当作批准；同一编号不能再决定，重新评审须提交更高版本。已用编号、过时版本、重复事件和错误命令失败关闭，不回落成新任务或猜测最近任务。

提案快照和决策保存在 SQLite 中，独立于普通聊天 `REPLY_TTL_SECONDS`，跨重启与数日后仍可核验；发送队列从每次入队起按 `REPLY_TTL_SECONDS` 到期（默认 1 小时，最多 24 小时）。记录存在不代表消息已送达，队列过期或发送结果 unknown 不会删除决定，也不自动重放。GitHub 提案及本人答复都会经过指定 Slack 私有频道；其中所有可见成员及应用都是潜在接收者，须一并审核。

决策回传的 metadata 为 `event_type: wecom_bridge_issue_decision`，`event_payload` 包含 `request_id`、`review_id`、`repo`、`issue_id`、`issue_number`、`revision`、`decision`、`reason` 及 `decision_record_only: true`；`decision` 分别为 `approve`、`reject`、`defer`。为支持不能稳定读取 metadata 的外部连接器，决定消息第一行精确为 `Issue decision record`，第二行为相同 `event_payload` 的单个 JSON 对象（包含 `reason`）；后续行为中文摘要及仅记录决定的提示。JSON 中 `<`、`>`、`&` 可使用 Unicode 转义，应按 JSON 正常解析，不用正则从理由中抽取决定。无论读 metadata 还是正文，都须先验证实际桥接 App 作者、固定私有频道与原提案线程，不能仅凭前缀相信内容。

提案标题、动作与范围在企业微信提示中显示为单行带引号的转义文本，避免正文伪造命令或提及；实际命令以提示独立给出的编号为准。可在 Issue 原根线程发送 `[wecom:owner]` 普通本人通知；`[wecom:review]` 和 `[wecom:group]` 被拒绝，不创建第二条评审链路。

HiveFlow 应用本版本只记录同意、不同意或暂缓并回传原提案线程，不把“同意”当成执行授权：不会运行代码、部署、自动写 GitHub、变更 Issue 状态或向群发布。外部 GitHub 评论及将来的其他写入或执行必须分别取得相应授权；决定记录本身不能扩大权限。


## 原文件：docs/MIGRATION.md

## HiveFlow 提取与单进程切换

本文是获授权运维的切换说明，不表示已执行生产操作。公开桥接器移除 Issue 业务代码及休眠控制模块；HiveFlow 持有它们，并注入公开运行时。此变更不新增企业微信连接、不更换 Bot ID、不新增服务，不导出/导入或搬走生产业务记录。

### 数据与代码所有权

- HiveFlow：`issue_proposals`、`issue_decisions` 的 schema、解析、版本与决定权限；`ISSUE_REPOSITORY`；独立休眠工作流控制模块。
- 桥接器：`requests`、`jobs`、`seen`、短期 `reviews`，认证、固定目标路由、发件箱与传输去重。
- 业务请求仍使用原 `kind='issue'`，保留与通用传输表的关联。共享 SQLite 文件允许决定和通知任务在同一个事务提交，不能拆库后声称仍具备这项原子性。
- 桥接器只清理自己的普通请求类型，不删除未知应用类型、历史业务表或其关联。无应用时，自定义请求拒绝投递，不能降级为普通聊天。无应用时发送循环跳过自定义 pending 任务，不因此修改其状态；通用 sending → unknown 启动恢复、明确投递到期及 prune 仍适用。永久审计不等于永久保留所有队列项，也不等于永久重发。
- `workflow.sqlite` 若已有真实数据也须原样保留；它是休眠控制状态，不替代 Issue 历史，不因提取覆盖或启用。

### 发布布局与离线门槛

将配套固定版本部署到同级只读代码目录，如 `/opt/hive-flow` 和 `/opt/wecom-slack-bridge`。HiveFlow 使用 `file:../wecom-slack-bridge`；发布记录必须同时标记两个代码版本及锁文件，不能让可变兄弟目录静默换成未验收版本。两边运行 `npm ci --ignore-scripts` 和 `npm run check`，只使用合成状态或另获授权的隔离副本。

组合入口为 HiveFlow `src/main.js`；通过 `startBridge({ createApplication })` 复用运行时。`initialize` 只初始化 HiveFlow schema，其他钩子为 `receiveRoot`、`receiveOwnerText`、`authorizeRequest`。公开桥接器不读取业务仓库配置、不导入 HiveFlow。

在副本上验证旧提案/决定、普通聊天、待处理回复、发件箱、去重及 TTL，比较行内容、ID 和关联，不只比较总数。故障注入须证明决定与发件箱一起提交或一起回滚。

### 获授权后的切换顺序

1. 清点既有 service、服务用户、环境文件、Bot ID 及绝对 `STATE_DIR`；这些值保持不变。记录两个代码版本、依赖版本、状态 schema 和 pending/sending/unknown/sent 分布，不把真实值写入仓库。
2. 停止既有服务并确认唯一写者已退出，不启动临时第二实例。准备一致 SQLite 备份并限制访问，采用 SQLite 备份机制或停写后正确处理 WAL 的完整快照；不能随手复制正在写入的主文件。保留所有历史、去重、队列及控制状态。
3. 部署配套代码及依赖。审阅同一个 service 的 `WorkingDirectory`、`ExecStart` 和沙箱路径，改到 HiveFlow 组合入口；保持原 `User`、环境文件和状态目录。工作目录变化不能把相对 `STATE_DIR` 悄悄变为新空库，须解析并固定原绝对路径。不要安装或启动第二个 unit。
4. 原环境文件中的业务仓库配置改由 HiveFlow 读取；不把生产配置搬到公开仓库。保持现有 Slack 作者/频道、本人和群边界，以及已授权响应方协议。不得借切换创建发现任务、改 webhook、启用执行器或自动模式。
5. 仅启动更新后的原服务。核验只有一个企业微信连接和一个状态写者，使用的是原数据库；检查原提案、决定、待处理编号、根线程、source 键、队列正文、目标及到期时间未被重写。
6. 在授权范围内验收普通聊天与跨切换待处理 Issue 回复。决定和通知送达分别记录结果；重启把 sending 变成 unknown 时，先核验外部证据，不改为 pending 盲目重放，也不转群。

### 验收要求

覆盖重复入站、同版本冲突、旧版本、defer 后新版本、重复决定来源、数日后回复、过期队列、pending/sending/unknown/sent、写入中断及重启。记录型批准继续带 `decision_record_only: true` 且不可执行。本人、频道或仓库不匹配时拒绝旧请求，不迁给新受众。未注入应用时保留业务记录但拒绝相关投递；重新注入后仍须核验权限和投递状态。

本地事务并不保证外部 exactly-once。不能仅凭单元测试宣称无重复消息或生产切换已完成；保存脱敏的实际验收证据，明确未运行、失败和通过项目。历史审计记录不得随本次提取删除。

### 回退

先停唯一写者并备份当前完整状态，保留切换后的新决定、去重和投递证据。在副本验证回退代码与当前 schema、TTL 清理及自定义类型兼容后，回退同一个服务的代码入口/版本，继续使用当前数据库。不能盲目恢复升级前快照，否则会丢失新决定并可能重放消息。

单独运行通用桥接器只会失败关闭应用类型，并不等于业务已恢复。若旧版本会删除业务映射、错误处理 unknown 或无法安全读取当前状态，保持停止并人工核验。旧备份不是自动重放来源。任何数据删除、额外数据迁移、发布和实际部署均不在本文自动授权范围内。

## 原文件：docs/OWNER_COMMANDS.md

## 本人私聊入口

只接受桥接已核验的本人企业微信私聊。仓库由部署方固定配置，群聊、他人私聊、转述和消息内伪造身份均不能获得执行权限。

### 常用操作

- `准备 #42 的执行方案`：由已配置的外部助手核对 Issue、提交与最小源码范围，准备版本化方案。准备本身不执行。
- `执行 #42 v1`：批准该 Issue 的这个明确版本。内部仍绑定唯一方案、提交、文件、操作、目标与有效期；不猜最近任务。原带 UUID 的命令兼容。
- `/plan #42 v1`：只读完整不可变方案，包括文件、提交和交付细节，不产生批准。
- `查询 #42`：读取已保存的方案、授权、派发和结果状态。
- `停止 #42`：撤销该 Issue 尚未结束的执行授权，锁住后续派发和阶段，并通知控制器协作停止已知任务。

问题号换成实际 GitHub Issue 编号。缺少版本、多个编号或含糊的“同意”不会启动执行。命令以机器人实际生成的方案为准。

### 自动协议（独立默认关闭，调度器尚未接通）

仅在部署方另行启用 `HIVEFLOW_AUTO_EXECUTION_ENABLED=true` 后接受开启和入队：

- `开启自动 2小时`：明确期限，也支持分钟或天，最长七天；已有会话不会因此延长。仅开启会话，不批准任何问题。
- `自动入队 #42 v1`：另行明确批准这个不可变版本进入当前会话；不能接管手动 `/execute` 请求或把旧 `/review` 决定转为授权。
- `查询自动`：查看会话、排队数量和共享槽状态，不授予执行权限。
- `停止自动`：立即禁止后续领取，标记活动任务请求停止；尚未核实真实任务终态时仍保留槽位。

一次只允许一个活动会话。重启、到期、额度未知或不高于 5%、失败或未知结果会暂停后续领取，不因额度恢复或 `completed` 收尾自行恢复。新会话不接管旧会话队列；需要新的明确方案授权。关闭配置后的历史查询和停止仍可用于恢复核查。

这只是可测试的会话/入队/领取协议。当前没有定时扫描、工作者创建或真实自动执行，不能把“已入队”当作“已启动”。

### 执行范围

只有显式启用手动协议并接通外部控制器，执行请求才可能进入实际工作。仅启动 HiveFlow 不会自行调用 Codex、扫描 GitHub 或部署项目。原 `/review` 仍只记录方案决定，不转换为执行授权。

具体方案可包含修改、测试、提交、推送、PR、合并和部署，但每项都必须明确列入。交付必须固定工作分支、目标分支及部署环境/服务、验证和回退要求。实际检查失败、额度不足或未知、范围变化时停止；不会自动启用自动模式。

### 状态与恢复

“已请求停止”不代表外部工作者已停止。占用槽必须保留，直到可信控制器核实真实终态；超时、断线或进程重启不会自动释放或重试。未派发的撤销授权不能继续派发。

结果作为不可变记录保存，同一结果重试不会重复通知。完成记录必须覆盖全部已批准操作及验证证据；真实外部状态仍须由控制器核对，不能用回报文本代替核验。

### 验收说明

仓库测试覆盖身份、版本、权限、幂等、事务与恢复状态。真实环境中的额度读取、任务创建、分阶段执行和部署必须分别验收，不能以单元测试或配置成功宣称完整闭环已验证。

## 原文件：docs/AUTO_MODE.md

## 默认关闭的自动会话、入队与领取协议

当前完整切片是本人会话/逐项授权入队/可信领取及既有结果控制。没有扫描器、定时器、工作者创建或真实自动执行器；全部测试使用合成数据。没有部署或启用生产自动配置，不能宣称自动开发全流程可用。

`HIVEFLOW_AUTO_EXECUTION_ENABLED` 独立于手动开关，仅精确接受 `true` / `false`，省略时为 false。首次默认关闭时不创建自动表；已有自动表时，即使关闭配置也先初始化重启恢复，将旧活动会话暂停、活动槽保留，再暴露入口。不能每次查询重新构造 AutoQueue。旧的独立 WorkflowControl 仍休眠，不能接成第二个调度器。

本人命令见 [简洁中文说明](#自动协议独立默认关闭调度器尚未接通)。只接受桥接核验过的本人企微单聊；群消息和 Slack 文本不能开启会话或批准入队。期限为明确的正整数分钟、小时或天，最长七天，没有默认期限或自动续期。

开启会话新建 `auto_session` 传输请求，其固定本人、仓库、私有频道和系统生成的 Slack 根保存在 `auto_protocol_sessions`。每条已处理命令以来源去重，状态、审计及回执在同一事务中提交。只在查询、合法状态访问或控制门检查期限，没有后台计时器。

`自动入队 #N vV` 必须唯一匹配当前仓库/本人/频道下最新且未过期的不可变方案，并另外登记明确自动授权和会话关联。它不读取旧评审批准，不写手动 `execution_requests`，不发 `hiveflow_execution_requested`。已有手动请求、手动授权或旧会话授权不能转为自动授权；自动授权也不能转为手动 `/execute`。新版本替代沿用同事务撤销和暂停保护。

### 可信领取控制

只有配置的可信 Slack 响应方在新根发送 `[wecom:auto-claim]` 后跟严格 JSON：

```json
{"command_id":"synthetic-claim-1","session_id":"stored-session-id","controller_run_id":"synthetic-controller","attempt_id":"synthetic-attempt","quota":{"remainingPercent":50,"observedAt":100000}}
```

示例是合成数据；真实额度必须由外部适配器核验来源、适用窗口与时效。`quota: null` 表示未知并持久暂停。不得附加接收人、频道、根线程、任意动作或方案。会话必须已获本人准许，且会话通知已在固定频道建立根线程。`command_id` 和 Slack 来源共同去重；同编号不同内容拒绝，重复不会再占槽或重发许可。

回执 metadata 为 `hiveflow_auto_claim_result`，只回会话创建通知的原根线程；输入的新根仅为控制命令载体。成功仅表示 reservation，`execution_started: false`，不消费执行许可、不创建外部任务。领取、审计和回执原子提交；额度未知、过时或不高于 5% 时，暂停与拒绝回执一并提交，发件箱失败则一并回滚。

后续复用 `[wecom:execution-control]`、持久结果与同一 ManualDispatch 槽。自动授权禁止从 `reserve` 旁路领取；`start` / `checkpoint` 必须走绑定会话的 AutoQueue 门。`started`、查询、停止、结果和终态仍可在配置关闭后恢复核查，不重新给予执行权限。失败/停止报告、unknown、停止、过期和重启都禁止下一次领取；活动槽仅在控制器核实真实终态及必要完成证据后释放。停止后即使后来记录 completed，也不能恢复会话。

尚未送出的成功领取/开始/阶段许可在发送前重新核验会话、配置、期限、授权和槽位；停止、重启、过期后不投递旧许可。拒绝、状态和结果回执仍作为事实送达。外部控制器仍须在每个真实动作前核对当前权威状态、额度、范围及任务身份；本地事务不能原子包住外部任务创建，也不能把消息送达当作任务已创建。

`auto_owner_commands`、`auto_claim_commands` 和会话关联用于持久审计；通用桥接继续只负责传输。实际调度、真实额度适配器、工作者创建及端到端验收均尚未接通。本轮不修改通用 bridge，也不执行真实 Issue。

### Core API (same Store and slot)

### Trusted API and authorization

Construct `AutoQueue(store, manualDispatch, { owner, repository, now, enabled })`
with the same authoritative bridge Store and ManualDispatch object. `enabled`
defaults to false. Owner and repository are fixed trusted configuration, not chat
arguments. Actor strings and quota snapshots are not authentication or proof:
a future adapter must verify the actual owner, source, immutable current plan,
repository/file scope, delivery binding, quota provenance and execution readiness.

- `start({ sessionId, actor, explicitOwnerStart: true, deadline })` records an
  explicit owner session with a future deadline no more than seven days away.
  Three days may be selected by a future user interface; there is no default
  implicit authorization. Only one active session exists. IDs cannot be reused.
- `enqueue({ sessionId, actor, grantId, explicitAutoAuthorization: true })` records
  separately authenticated owner intent to queue that exact immutable grant.
  It requires a matching owner and repository, an unused, unrevoked, unexpired
  explicit execution grant, and no prior attempt. It persists owner and
  `explicit_auto_queue` provenance. This marker does not itself authenticate the
  caller. Review-only approvals are never read or promoted to grants.
- Existing manual `/execute` requests are excluded by grant or plan identity at
  enqueue and again at claim, even if their manual notification has not delivered.
  There is no current automatic grant ingress. A future authenticated auto ingress
  must create its own explicitly authorized immutable grant without a competing
  manual dispatch notification; that integration is outside this change.
- `claim({ sessionId, controllerRunId, attemptId, quota })` selects the oldest
  eligible queued grant. Expired, revoked, consumed, attempted, or newly manual
  candidates are marked ineligible. Exact grant IDs are globally deduplicated;
  old queue entries never move into a later session, and neither cancellation nor
  failure permits automatic retry. A later attempt needs a newly approved grant.

### One pipeline and fail-closed continuation

Queue selection and `ManualDispatch.executeInTransaction('reserve', ...)` share
one Store transaction. There is no second pipeline lock. A manual or automatic
reserved, dispatch-started, running, or unknown attempt holds the same existing
single slot. Queue-write failure rolls back reservation. Claim is not dispatch
or evidence that a worker exists. No grant is consumed until startDispatch.

When a trusted ingress already holds the Store transaction, use
`executeInTransaction(method, args)` for `status`, `start`, `enqueue`, `claim`,
`executionGate` or `stop`. This explicit seam does not open a nested transaction;
calling it outside a transaction or selecting an internal method is rejected.
Handle expected quota/session rejection **inside** the outer transaction, record
the rejection audit and outbox there, then commit so the pause remains durable.
If the audit/outbox fails, roll back the entire transaction, including the pause.
Letting a rejection escape the outer transaction also rolls everything back.
No interface can commit a pause independently while promising atomic outbox.
Construction and restart recovery still happen before exposing ingress.

Use `executionGate({ sessionId, method: 'startDispatch' | 'checkpoint', ...args })`
for every future auto execution stage. It verifies session membership before
calling the existing ManualDispatch protocol in the same transaction. Scope,
controller identity, consumed grants and delivery targets retain their existing
checks. The tiny ManualDispatch grant guard also checks an auto-owned attempt's
persisted session status/deadline, so a direct manual control call cannot bypass
an expired or stopped automatic session. Session denial durably latches the
attempt's stop flag; the control protocol recognizes it as a rejected operation.

Queued auto grants cannot be taken over by direct manual reservation. The atomic
claim binds the queue entry before reserving; both writes commit together. Direct
manual control quota failure, stop, unknown outcome, revoked active grant, or a
stopped terminal outcome also pauses its bound auto session. Releasing the slot
after an uncertain/failed/stopped task does not authorize the next queued task.
Only a verified successful completion leaves an otherwise valid session active.
Authenticated owner stop and plan-version replacement also pause any session
containing the revoked grant, even if that grant is still queued. They latch the
session's current worker stop flag and retain its slot in the same transaction
as revocation, ingress audit and outbox. A later `completed` report cannot resume
that session or authorize its next queued grant; failed outbox rolls back the
entire change. No dormant queue tables are created by these ingress paths.

Unknown, invalid, future-dated, or older-than-60-second quota, and remaining quota
at or below five percent, pause the session durably. A fresh observation does not
resume it. The future adapter must supply trusted live quota evidence. There is
no job-count allowance: the bounds here are the explicit deadline, individually
approved grants, quota, and single concurrency.

`stop({ sessionId, actor })` prevents further claims immediately and latches stop
on that session's active attempt only. Expiry is evaluated at each state access
or execution gate; there are no background timers. Create one AutoQueue per process/controller lifetime, not per query. Creating
another AutoQueue instance represents controller restart and durably pauses all previously active
sessions, including when the new instance is disabled. It never adopts or replays
an old attempt. Future integration must construct this recovery component before
exposing any dispatch/control ingress; ManualDispatch alone cannot detect a
process restart. Stop, expiry, quota failure and restart never release a live or
unknown slot and never interrupt an already in-flight external side effect.
Actual interruption/reconciliation remains the trusted controller's duty.

`status({ sessionId })` reports state, ordered queue and shared active attempt;
`executorImplemented` remains false. A trusted controller must verify the worker
stopped or was never created before using the existing terminal protocol. Neither
completion nor improved quota renews a session. A new session requires a fresh
explicit owner start, and no old queued grants are carried forward.

### Validation and remaining integration

`node --test test/auto-queue.test.js` covers synthetic owner/repository isolation,
manual-request exclusion (including after enqueue), FIFO/deduplication, atomic
rollback, shared-slot contention, unknown outcomes, deadline bounds, stop,
restart, stale/low/unknown quota, durable pause and direct manual-route expiry
fencing. `npm run check` runs the entire local suite.

Before any runtime activation, separately implement and approve authenticated
owner commands, distinct auto-grant ingress, trusted quota retrieval, live task
creation and cancellation, source/symlink scope checks, failure handling, result
verification, and deployment/rollback acceptance. These tests do not establish
that those integrations exist or that automation is ready to enable.
