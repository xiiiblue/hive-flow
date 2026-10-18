# HiveFlow

HiveFlow 是一套供智能体遵循的研发协作规范：先调查反馈，再由负责人决定是否采纳；实施时一次说清范围，完成后按证据交付。它不提供模型、机器人、业务服务或任务调度器。

HiveFlow is an agent collaboration specification, not a deployable service. The documentation is currently maintained in Chinese.

## 从哪里开始

|你要做什么|阅读|
|---|---|
|了解角色、授权和交付|[核心流程](docs/WORKFLOW.md)|
|处理群咨询、Bug 或需求|[群入口](docs/GROUP_INTAKE.md)|
|把规范映射到现有工具|[工具适配](docs/ADAPTERS.md)|
|写清楚通知和追问|[交互示例](docs/OWNER_COMMANDS.md)|
|核对边界与验证程度|[验收清单](docs/ACCEPTANCE.md)|
|修改规范|[贡献说明](CONTRIBUTING.md)、[项目规则](AGENTS.md)|

本仓库没有需要安装或启动的应用。文档检查只需要 Git 和 Python 3.9+：

```sh
python3 scripts/check_docs.py
```

## 当前采用的方式

- 负责人在 **dots 原生本人主对话**评审、授权并接收私有结果；企微审批和历史执行许可均不启用。
- 已授权临时群用于咨询、反馈和可公开的登记回执；群内容不授予研发执行权限。
- dots 先在自己的云端核对固定仓库代码/文档；必要的环境查询由已连接的 Mac mini Codex medium 受限只读完成。
- 手动任务按有效授权分阶段推进。**自动开发关闭**；普通“继续”、既有反馈任务或历史批准都不能开启它。
- 通用 [wecom-slack-bridge](https://github.com/xiiiblue/wecom-slack-bridge) 独立维护，采用传统接收确认与主动普通回复。流式演示已撤回；本规范不保证原生引用、真正 @ 人或群消息自动续接。
- 不进行主动额度查询、余额阈值或快照门禁；实际平台失败仍须如实报告，不盲重试。

仓库地址、群/成员标识、环境地址和账户权限保留在各宿主已有的受控配置中。采用本规范不会获得这些权限，也不会自动修改已启用任务。具体能力与未验事项见 [适配状态](docs/ADAPTERS.md#能力与验证状态)。

## 维护与发布边界

当前文件只保留在用规范和文档检查。退役的 Node 应用、旧命令协议、自动开发设计与迁移流水保留在 Git 历史，不再提供启动入口；未提交的本地旧实现不能被当成已发布代码。

规范变更不等于部署生效、集成通过或取得执行授权。公开前仍需审阅 Git 历史与第三方权利；本次整理不改变仓库可见性，不发版。

**许可证尚未选定，仓库没有 LICENSE 文件。** 发布为开源项目前须由负责人决定许可及贡献授权方式，本仓库不预设该决定。敏感信息处理见 [安全说明](SECURITY.md)。
