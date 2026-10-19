# HiveFlow

HiveFlow 是源自蜂巢实践的研发协作规范，探索从 Director 逐步协调，走向授权边界内的流程自动承接。先调查反馈，由负责人决定是否采纳；实施时一次说清范围，完成后按证据交付。当前自动开发尚未启用。

它复用现成的理解、工单和沟通能力，不从零构建模型、工单系统或即时通信平台，也不提供新的业务服务或任务调度器。

HiveFlow is an agent collaboration specification, not a deployable service. The documentation is currently maintained in Chinese.

## 复用现成能力

|能力|在 HiveFlow 中的职责|
|---|---|
|Dots 及其可用模型|理解问题、调查事实、整理方案与协调工作；复用账号可用的先进模型能力，具体模型以账号和平台支持为准|
|GitHub Issues|管理 Bug、需求和交付线索；登记、采纳与实施分别核验，不把工单状态当作执行许可|
|企业微信与独立桥接|作为日常工作沟通入口，经 [wecom-slack-bridge](https://github.com/xiiiblue/wecom-slack-bridge)连接到已授权响应方；桥接只传输消息，不判断业务或调用模型|

这些是规范中的职责分配，不表示任意账户、工作群或集成已经接通。管理员须分别完成现有平台的接入与授权。

规范仓库维护规则与指南；[展示网站](https://www.bluexiii.com/flow/)由独立的 [flow 仓库](https://github.com/xiiiblue/flow)维护并发布审阅后的公开快照。网站不是运行服务，也不会修改群配置或工具权限。

## 从哪里开始

|你要做什么|阅读|
|---|---|
|每次任务加载当前规范|[统一加载入口](docs/SPEC_ENTRY.md)、[加载契约](docs/SPEC_LOADING.md)|
|第一次安装理解与离线演练|[快速开始](docs/QUICKSTART.md)|
|记录采纳/拒绝/暂缓与交付收尾|[事项状态与结项](docs/ITEM_LIFECYCLE.md)|
|在企微群提问、报 Bug、提需求|[用户使用指南](docs/USER_GUIDE.md)|
|了解角色、授权、审阅/完整交付两种终点|[核心流程](docs/WORKFLOW.md#交付模式)|
|准备两种原生定时任务及采纳选单|[自动化模板](docs/AUTOMATION_DESIGN.md#两种可复制原生任务模板)，规范与离线辅助，未启用|
|处理群咨询、Bug 或需求|[群入口](docs/GROUP_INTAKE.md)|
|在已授权范围内缓存与静态查询代码|[云端过滤源码快照](docs/SOURCE_SNAPSHOTS.md)|
|把规范映射到现有工具|[工具适配](docs/ADAPTERS.md)|
|写清楚通知和追问|[交互示例](docs/OWNER_COMMANDS.md)|
|核对边界与验证程度|[验收清单](docs/ACCEPTANCE.md)|
|修改规范|[贡献说明](CONTRIBUTING.md)、[项目规则](AGENTS.md)|

本仓库没有需要安装或启动的应用。文档检查只需要 Git 和 Python 3.9+：

```sh
python3 scripts/check_docs.py
```

受控采纳的固定位置、dots 生产与 Codex 认领见[单槽契约](docs/ADOPTION_RECORDS.md)。本仓库不保存运行记录；当前控制分支/文件尚未创建。

## 当前采用的方式

- 群反馈采纳评审与 dots 协调任务的授权、私有结果在 **dots 原生本人主对话**；本人直接 Mac mini Codex 手动实施按 [本人直接入口](docs/WORKFLOW.md#本人直接-codex-手动入口)在实际宿主核验，不迁回 dots 重审；企微审批和历史执行许可均不启用。
- 管理员接入并授权的工作群用于咨询、反馈和可公开的登记回执；适用范围以实际接入配置为准，群内容不授予研发执行权限。
- dots 先在自己的云端核对固定仓库代码/文档；必要的环境查询由已连接的 Mac mini Codex medium 受限只读完成。
- 手动任务按有效授权分阶段推进。**自动开发关闭**；普通“继续”、既有反馈任务或历史批准都不能开启它。
- 通用 [wecom-slack-bridge](https://github.com/xiiiblue/wecom-slack-bridge) 独立维护，采用传统接收确认与主动普通回复。流式演示已撤回；本规范不保证原生引用、真正 @ 人或群消息自动续接。
- 不进行主动额度查询、余额阈值或快照门禁；实际平台失败仍须如实报告，不盲重试。

仓库地址、群/成员标识、环境地址和账户权限保留在各宿主已有的受控配置中。采用本规范不会获得这些权限，也不会自动修改已启用任务。具体能力与未验事项见 [适配状态](docs/ADAPTERS.md#能力与验证状态)。

## 维护与发布边界

当前文件只保留在用规范和文档检查。退役的 Node 应用、旧命令协议、自动开发设计与迁移流水保留在 Git 历史，不再提供启动入口；未提交的本地旧实现不能被当成已发布代码。

规范变更不等于部署生效、集成通过或取得执行授权。公开前仍需审阅 Git 历史与第三方权利；本次整理不改变仓库可见性，不发版。

**许可证尚未选定，仓库没有 LICENSE 文件。** 发布为开源项目前须由负责人决定许可及贡献授权方式，本仓库不预设该决定。敏感信息处理见 [安全说明](SECURITY.md)。

手动研发交付及 [默认 HTTP 收尾](docs/MANUAL_DELIVERY.md#默认交付收尾)由 hive-flow 统一维护：先识别现有可信绑定，实际交付/验收完成后自动生成必要脱敏回执；无绑定正常结束注明，不扩大合并部署等当次权限。默认 Mac mini Codex 手动处理，自动开发保持关闭。

后续自动研发采用 [Codex 桌面原生 Automations](docs/AUTOMATION_DESIGN.md)：指定本地 HiveOS 项目、原生 worktree、任务指令及 ACTIVE/PAUSED；不自建 CLI 调度或批次控制层。本人已在本地创建 PAUSED 配置；业务范围、期限、数量和启用未获完整配置/授权，实际 prompt 与运行历史尚待原生核验；互斥、停止和去重按实测或流程约束说明，不构成硬隔离保证。
