# HiveFlow

HiveFlow把反馈、采纳决定、研发和交付结果连起来。云端dots调查并登记GitHub Issue，Issue之后由Director的MacMini上的Codex承接；手动启动和原生定时任务，都可以选择PR待审或完整交付。当前业务自动执行尚未启用。

本仓库是HiveFlow的**私有固定版本副本**，用于独立阅读、离线检查和未来开放前的整理。唯一权威规范仍在HiveOS的`docs/06-自动化/`，介绍手册是网站的原始材料；内容依赖为**HiveFlow规范→HiveFlow介绍→HiveFlow网站**。真实任务继续从HiveOS核验版本和加载规则，本仓库不重新成为调度入口。

当前副本来源：HiveOS提交`4d4f9090aeac6638c424c39d2abbed5ce6183979`。原始摘要、导出摘要及少量导航整理见[来源清单](export-manifest.json)。未导入HiveOS业务源码、宿主配置、消息记录或运行数据。

## 从哪里开始

|你关心什么|阅读位置|
|---|---|
|第一次了解，想按场景读懂整套方法|[介绍手册目录](docs/11-手册/05-自动化手册/README.md)，包含12章正文和3篇附录|
|Issue之后在哪里做，Director需要介入几次|[流程总览](docs/11-手册/05-自动化手册/03-流程总览：一条反馈怎样走到交付.md#9director需要介入几次)|
|停在PR待审，还是完成合并、部署和验收|[两种交付终点](docs/06-自动化/HFL-003-协作流程与授权.md#交付模式)|
|查正式规则及每份文档的职责|[HFL规范目录](docs/06-自动化/README.md)，共19份编号正文|
|理解原生定时任务、受控采纳和认领|[HFL-009](docs/06-自动化/HFL-009-原生自动化任务模板.md)、[HFL-010](docs/06-自动化/HFL-010-采纳记录与任务认领.md)，模板保持暂停|
|维护这个副本，或核对它与HiveOS的差异|[副本维护与同步](docs/维护/EXP-001-副本维护与同步.md)|
|了解以后开源还要做什么|[公开前准备清单](docs/维护/EXP-002-公开前准备清单.md)|
|核对本次实际检查和未验证范围|[验证记录](docs/维护/EXP-003-验证记录.md)|

[展示网站](https://www.bluexiii.com/flow/)及[flow网站仓库](https://github.com/xiiiblue/flow)提供公开介绍。网站取材于介绍手册，不直接把私有执行协议或运行记录发布出去。

## 四种组合，两个独立选择

|本地启动|PR待审|完整交付|
|---|---|---|
|手动|Director发起1次；开发、检查和PR完成后停止，再介入1次处理合并、部署|Director明确完整范围并授权1次，正常路径连续推进到约定交付|
|原生定时任务|有效配置内无需逐单发起；每单PR完成后仍需介入1次|有效配置内无需逐单发起或例行确认后续步骤|

这里只统计Issue进入MacMini后的研发阶段；源头采纳、定时配置批准与启用另计。完整交付仍须真实完成规定审阅和约定验收，约定本人验收、出现新的重要取舍或超出授权时，按实际情况再介入。不能把次数当作授权，也不能把已有模板当作业务自动执行已启用。

## 目录与离线工具

```text
AGENTS.md                         本副本的维护入口
export-manifest.json              固定来源及逐文件摘要
docs/06-自动化/                   HFL-001至HFL-019、目录与机器清单
docs/11-手册/05-自动化手册/        介绍手册正文及附录
docs/维护/                       副本维护、公开准备及验证记录
scripts/hiveflow/                 来自HiveOS的离线工具与测试
scripts/sync_from_hiveos.py       从已选HiveOS提交更新副本
scripts/check_repository.py       全仓链接、来源摘要与常见敏感形态检查
```

Python3.9+和Git即可完成文档及纯数据检查，不需要安装业务服务。macOS先设置`TMPDIR=/tmp`；本机优先使用已配置的Miniconda解释器。

```sh
export TMPDIR=/tmp
python3 -B scripts/check_repository.py
python3 -B scripts/hiveflow/test_spec_loading.py
python3 -B -m unittest discover -s scripts/hiveflow -p 'test_*.py'
python3 -B scripts/test_export.py
git diff --check
```

工具只在明确指定的本地目录中执行离线数据处理或合成演练，不自动配置任务、建Issue、发送回执、部署服务或申请权限。需要本地桥接仓库与依赖缓存的接入演练单独选择，本次纯规范同步不运行真实平台链路。

## 副本怎样保持可信

规范和工具先在HiveOS修改并验证，再按完整提交导出。本仓库保留上游目录结构和HFL编号；未收录的ACDL、HiveOS根规则与其他手册，链接固定到同一上游提交，访问仍需原有私有权限。

[来源清单](export-manifest.json)分别记录原始字节和整理后字节的SHA-256，不把导航改写冒充逐字复制。业务规则与离线工具不在本副本另行修改；副本维护工具和说明由本仓库维护。源文档中的Accepted、Review、日期和验收事实均属于来源版本，不代表在本仓库重新验收或持续有效。源规范中针对HiveOS的`pnpm`命令和根AGENTS要求仍适用于HiveOS，本仓库检查使用上面的命令。

旧英文正文和旧应用从Git历史追溯，当前树不保留第二份规则或旧加载兼容入口；文件名对照见[HFL-018](docs/06-自动化/HFL-018-来源迁移与路径对照.md)。

## 当前仍闭源

仓库继续私有，尚未选择开源许可证或开放外部贡献。未添加LICENSE，也未修改仓库可见性、分支权限或发布设置。历史、附件、权利、产品专用事实及私密报告方式仍需在正式开放前逐项审阅，不能把“当前文件检查通过”写成“整个历史已可公开”。维护方式见[贡献说明](CONTRIBUTING.md)，敏感问题见[安全说明](SECURITY.md)。
