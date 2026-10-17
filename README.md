# HiveFlow

HiveFlow 是围绕 GitHub Issue 的私有协作工作流项目，沿用 ACDL 协作标准。HiveOS 是首个使用场景，不是硬编码依赖。仓库名为 `hive-flow`；本地代码变更不代表已推送、发布或部署，不假设开源许可证。

## 当前实现与边界

HiveFlow 的组合入口导入公开 `wecom-slack-bridge/runtime`，注入可信 Issue 应用。同一进程复用桥接器的 Slack / 企业微信连接：只有一个企业微信连接，不另起机器人或第二个服务。

- HiveFlow 拥有 Issue 解析、不可变身份与修订、提案/决定 schema、业务配置和权限校验。
- 通用桥接器拥有认证、路由、发件箱、传输去重和短期本人问答关联；它不依赖 HiveFlow，不读取 `ISSUE_REPOSITORY`，也不提供 Issue 工作流。
- Issue 决定与发件箱继续使用同一 SQLite 文件、同一事务。此提取是逻辑所有权和代码边界调整，不是物理数据搬迁；已有记录、ID、历史及待处理回复必须保留。
- 新增独立执行方案预览及版本绑定的手动请求入口；默认执行器未接入，请求返回 `not_ready`；新增显式部署开关仅启用手动协议和独立许可，不证明外部执行器就绪或自动启动工作，见 [手动执行边界](docs/MANUAL_EXECUTION.md)。
- `src/workflow-control.js` 仍是独立休眠控制基础，默认 `executionReady: false`，不是扫描器或执行器。现有 `decision_record_only: true` 决定永远不自动升级为执行许可。
- 新的休眠自动队列 `src/auto-queue.js` 与手动派发共用一个 Store 和执行槽，覆盖明确入队、期限、停止及异常暂停；尚未接入本人命令、定时器或真实任务。不要把旧独立控制库接成第二个调度器。详见 [自动队列边界](docs/AUTO_MODE.md)。

## 本地验证

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

## 运行与切换

组合服务由 HiveFlow `src/main.js` 启动。既有部署保留原服务身份、环境文件和绝对 `STATE_DIR`，只在获授权且通过离线验证后更新同一个服务的入口。不要同时运行两个仓库的 `npm start`。共享状态的单写者切换、一致备份、验收和回退见 [迁移说明](docs/MIGRATION.md)。

Issue 协议及控制能力边界见 [工作流约定](docs/WORKFLOW.md)。单元测试不证明生产迁移或端到端验证已经完成。部署、自动发现、执行适配器及有期限自动模式均须分别授权和验收。
