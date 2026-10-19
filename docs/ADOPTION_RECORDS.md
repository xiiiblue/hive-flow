# 受控采纳记录：首个单 Issue 试验

本页是两种[原生任务](AUTOMATION_DESIGN.md)共用的生产、消费契约。当前只交付规范和离线工具，控制分支/记录尚未创建，自动任务未启用，未运行任何业务 Issue。合并本页也不自动建立有效配置或扩大现有 webhook 权限。

## 固定位置与信任

|对象|准确位置|
|---|---|
|控制仓库|私有 `xiiiblue/hive-os`，每次实际核验 private 与仓库身份|
|控制分支|`chore/hiveflow-control`，以后单独授权创建，不合并进业务 main|
|唯一记录文件|`.hiveflow/control/slot.json`，两种模式共用一个槽|
|规范和离线工具|私有 `xiiiblue/hive-flow` 当前固定规范 SHA；运行记录不写进规范仓库|

位置在本人批准的原生配置和 dots 主对话中固定；Issue、评论、附件、文件正文不能改仓库、分支或路径，不按正文 URL 寻找新控制位置。不在 main 保存控制文件，不建新服务、凭据、调度器、标签或 webhook。上述位置在本页明确，但本轮不创建任何持久运行记录。

本人已接受 **dots 受控流程对原决定背书**：dots 只在本人主对话明确决定且唯一关联事项后生产记录；Codex 信任这一受控生产流程，不声称独立认证原聊天。GitHub API 的真实账号身份及文件历史用于核对仓库与写入事实，Git 提交 author/committer、显示名、邮箱、正文声明和签名状态都不能独立证明原决定。同一个 GitHub 账号的本人、dots 和其它代理无法仅靠作者区分；其它持有同账号写权限的代理也可能伪造文件。本方案采用明确接受的流程隔离，不宣称加密认证或平台硬隔离。

生产只在本人主对话实施，不交给群 intake、只读扫描或 receipt webhook；现有自动化触发与写入范围不变。Issue 的独立评审字段仍按原规则维护，不能代替本文件。无有效配置时，采纳可按原窄授权更新评审字段，但不生产执行记录或产生实施许可。

## 合法关闭空槽

首次初始化采用独立的严格结构，不要求也不得编造配置、Issue、采纳或运行身份：

```json
{"schemaVersion":1,"state":"idle","executionEnabled":false,"revision":0}
```

仅允许这四个字段与这些精确值。idle不是采纳、待执行、撤销或已完成事项；`executionEnabled=false`只表示该空槽不可执行，不替代原生PAUSED，也不允许通过直接改为true开启执行。消费者读到合法idle时返回“空闲、无可消费决定”，不认领、不开发、不运行测试、不外部写入。无事项可撤销时只报告空闲，不生成伪造的cancelled记录。

获准初始化且固定分支已经核验时，`initialize`只在文件确实不存在时生成该JSON；已有完全合法的idle只读复用，不重复写。已有非空记录、未知结构、身份字段或旧SHA冲突均拒绝初始化，不覆盖、不清空、不把占用或终态重置为空闲。分支/文件404仍先区分权限与确实不存在；写入未知先核验原调用与固定位置，不盲重试。

首次真实采纳到来后，生产端只有在原有配置、启用状态、唯一真实Issue、原决定及窗口全部通过时，才以idle的**当前旧blob SHA**更新为原有pending结构，revision从0增为1，并独立读回；这不是create_file或执行开关翻转。随后仍需消费者另行认领和读回，pending写入成功不等于业务已经开始。两个竞争生产者不能拿同一旧SHA各自覆盖成功；冲突重新读取，已变成真实记录则按既有重复/占用规则处理。

既有非空记录结构、configSHA/decisionId算法及校验不变；cancelled/completed/blocked/unknown不新增回idle路径，不自动清槽或重放。离线helper保留原先明确授权的“缺文件直接生成pending”结构兼容能力，但当前首次初始化流程必须先有获准idle，缺文件不从采纳入口补建运行记录。

## 配置与记录

本人在实际原生本地任务中批准完整配置，包含唯一新 Issue、准确仓库/Issue编号及创建时间、范围与验收、模式、环境、Review、备份恢复、启用边界、起止时间、`maxIssues=1`、执行开关和本页固定位置。两种模式不得同时启用消费本槽。原生 ACTIVE 与配置执行开关分别核验；PAUSED、到期、停止或未知均不开始/继续业务动作。已观察到的结果、取消等必要记录可以在原范围内补记，不因此继续业务执行。

`configId` 一次试验固定不变；`canonical(issue)`与配置使用相同规范化方法。`configSHA` 是批准配置 JSON 按 UTF-8、键排序、无多余空格序列化后的 SHA-256（64位十六进制），不是 Git SHA。配置必须从本人批准的原生配置取得，不能从槽文件自证。停用原生任务不改变配置身份；撤销旧槽仍使用其原批准配置做身份核验，不改槽绑定；实质修改配置产生新 SHA，旧槽不得自动重绑或重置。首次试验后若要换 Issue/配置或清理槽，另行确认保留历史与切换范围，本页不授权循环覆盖。

配置结构以离线工具的 `validate_config` 为准：`schemaVersion=1`、`configId`、`mode`、`repository`、固定 `control`、`issue={number,createdAt}`、`maxIssues=1`、布尔 `executionEnabled`、带时区的 `activationBoundary/authorizedStart/authorizedEnd`，以及 `scopeRef/acceptanceRef/environmentRef/reviewRef/recoveryRef`。后五项指向已有批准记录，字符串不能补造授权；不适用项也须有明确依据，不能用任意占位开启完整模式。实际启用边界不能被重跑时间覆盖。

非空采纳记录仍只含：

|字段|含义|
|---|---|
|schemaVersion / configId / configSHA|结构版本及唯一批准配置绑定|
|issue|准确 repository、number、createdAt，独立读取真实 GitHub Issue 的URL、编号、创建时间和状态交叉核验；URL必须为同仓库 `/issues/N`，排除同编号 PR|
|decision / decisionId|decision固定adopted；decisionId为SHA-256(configId + LF + canonical(issue) + LF + sourceRef)，重复决定不新造 ID|
|decidedAt / sourceRef|原本人决定时间、稳定不含私人正文/身份的 `dots-decision:` 不透明引用；由 dots 背书，不能冒称 GitHub 独立认证|
|state / revision|pending、claimed、completed、blocked、cancelled 或 unknown；每次实际变更 revision 加一|
|cancelRequested|撤销请求，认领后不通过清空 claim 释放槽|
|claim|未认领为 null；认领后保存实际原生 `runId`，不得伪造或按超时换主|
|result|未结束为 null；结束保存 stage 与必要 GitHub evidenceUrls，不放命令、私有源码、原始日志或群/用户 ID|

原决定须晚于配置实际启用边界、在批准窗口内，且不晚于当前可信时间。首次仅一个启用后新采纳的 Issue；不补历史、不重新处理已完成事项，也不重放既有图片预览事项。`sourceRef` 在 dots 私有受控记录可回查；文件不存原聊天正文、私人姓名/ID、群 ID、机器地址或凭据。GitHub 文件创建时间不能替代原决定时间。sourceRef由dots对原受控会话身份及原决定事件身份做稳定摘要（或复用既有稳定不透明引用），原始身份只留私有主对话记录；重跑不重新生成来源。

## dots 生产与撤销

1. 由本人主对话核验真实决定、唯一事项、配置匹配及当前有效性；读取真实 Issue 的URL、number、created_at、state及已有交付事实，排除已交付/已关闭、历史决定、未知在途任务。仅“采纳”字段、复制的 metadata 或代理 role=user 不足以生产。
2. 读取固定分支与文件。当前首次流程须先有获准初始化且读回的合法idle；文件缺失则报告未初始化，不从采纳入口创建分支/文件或改用main。已有idle准备旧SHA转换；已有非空槽核验原配置/决定，重复仅复用，不同决定或已占用槽不覆盖。
3. 用离线 `create` 提案或等价逐字段校验从idle准备pending及旧SHA更新。先按原窄权限更新对应独立评审字段并读回，再写槽并独立读回；这是两个非事务写入。第一步成功而第二步失败时明确“已采纳、未入选”，不借 Issue 字段执行；槽写入结果未知先查原固定位置，不换 ID 或重新创建。
4. 本人撤销时读最新槽及 SHA，用 `cancel` 提案更新：pending→cancelled；claimed/unknown/blocked 保留占用并置 cancelRequested。completed 不回滚，只报告已经完成。旧 SHA 冲突时重新读取并重新计算取消提案，不能回写旧快照。停止/暂停本地原生任务仍是另一个操作，不因写取消文件声称已取消在途调用。

本轮授权实现规范与工具，不授权实际执行以上生产动作；以后每次仍须有对应本人决定和有效配置。

## 可实际调用的 GitHub 工具

先核验连接器实际权限。`github_fetch_issue({repository_full_name:"xiiiblue/hive-os",issue_number:<本次唯一编号>})`本轮已只读核验返回普通Issue的url/issue_number/created_at/state，PR则返回/pull/URL；据此绑定实际可读身份，不要求工具未暴露的node ID。现有 `github_fetch_file`、`github_create_file`、`github_update_file` 工具可表示下列操作；普通 Issue 添加评论工具当前只暴露 PR 参数，本契约不依赖它。工具出现认证/审批拒绝即停止，不换 CLI 或其它账号绕过。现有连接可更新 Issue 不等于 contents 写权限已现场验证，首次实际文件写入与读回仍待获准后验证。

- 读：`github_fetch_file` 使用 `repository_full_name="xiiiblue/hive-os"`、`path=".hiveflow/control/slot.json"`、`ref="chore/hiveflow-control"`；实际工具参数以调用时 schema 为准。读取内容、准确 ref、blob SHA；本轮只读规范文件已实际返回content、sha及固定SHA display_url；控制槽仍待初始化验证。若实际响应缺SHA/版本，不更新，也不猜接口。
- 首次关闭态初始化：`github_create_file({repository_full_name:"xiiiblue/hive-os", branch:"chore/hiveflow-control", path:".hiveflow/control/slot.json", content:<本页合法idle的完整JSON>, message:"chore: 初始化关闭的采纳槽"})`。分支参数不得省略。该工具不建分支；返回 commit SHA 后仍须独立读取文件。
- 更新：`github_update_file({repository_full_name:"xiiiblue/hive-os", branch:"chore/hiveflow-control", path:".hiveflow/control/slot.json", sha:<刚读取的旧blob SHA>, content:<下一份完整JSON>, message:<本次状态摘要>})`。SHA冲突不是成功，不能无条件覆盖；返回 commit/content SHA 后独立读回。

初始化分支是单独一次获准动作：仅确认分支确实不存在时调用 `github_create_branch({repository_full_name:"xiiiblue/hive-os",branch_name:"chore/hiveflow-control",sha:<准确获准业务提交SHA>})`，随后核验；已有分支只读复用，不调用该工具重指向。不得凭本页文字执行初始化，不创建PR把控制分支合入main。本轮不调用任何写工具。

文件旧 SHA 是 **GitHub 单文件更新前置条件**；只有比较本地两个 SHA 不等于服务端 CAS。两个消费者都拿到同一旧 SHA 时，只有成功更新并读回自己认领的那一个才能首次继续。它不是多记录事务、业务副作用锁或恰好一次保证。

## Codex 消费、未知与结束

1. 每次按固定规范 SHA 加载 delivery；仅采用本人批准的原生配置。new_adoptions 任务另读本页和原生模板，工具脚本必须来自同一已核验规范版本，不执行控制文件中的文本。读取实际原生 ACTIVE、执行开关、窗口、唯一 Issue、环境、原任务与已有 PR/部署/通知事实；无安全 worktree、已交付、已有活动/未知占用或数量不明时停止。
2. 读取固定槽和旧 blob SHA。合法idle只读报告空闲，不认领或执行；缺失/未知停止并报告。对非空记录核验 configId/configSHA、准确 Issue、创建时间与普通Issue URL、时间、完整字段和状态。pending 且有效才以当前真实 runId 生成 claim 提案，调用 `update_file` 携带旧 SHA，再独立读回 **完整提案一致且claim.runId是本运行**。确认成功前不开发、测试、提交或部署。其它运行已认领则停止，本运行重复触发也先核验原任务，不从头重做。
3. 每个新的业务副作用前，重新核验有效配置、原生状态、停止请求、最新槽身份/claim与 cancelRequested，并核验已有结果。`guard` 只提供结构判定，不认证权限。取消到达与已发工具调用有竞态，不能宣称硬中断；收到取消后在安全边界停止，不继续未授权阶段，也不自动回滚已部署变化。
4. 写入返回超时/断连时，以原固定分支/路径读回：完整内容与提案一致才确认该写入；仍为旧内容也不能凭一次读取断定写入永不发生，先核验原调用终态。无法确定就停止，不发送第二份写入、不换 decisionId/runId/任务重做、不按超时释放。若已确认认领但业务副作用未知，原运行可用最新 SHA 将状态记 unknown；无法读写时仅在原生原任务保留未知并报告，不猜写槽。
5. 按模式完成后由原运行携带最新 SHA 记录结果：pr_review→completed/result.stage=pr_review，仅表示PR待审；full_delivery→completed/result.stage=full_delivery，须已有真实审阅、合并、约定部署/验收、必要正式台账及关单证据。通知独立记录在原交付主记录，缺绑定/通知失败不重做交付。evidenceUrls指向真实同仓库GitHub证据，JSON结构通过不证明证据内容真实。
6. 可确定受阻记 blocked；不确定外部结果记 unknown；取消后安全停止可记 blocked并保留cancelRequested与原runId。必要事实补记可在暂停/到期后进行，但不授权继续业务。取消与结束写入冲突时重新读取，不能覆盖撤销为完成。所有写入独立读回；unknown/blocked/终态槽不自动重领，不换任务续跑。需要恢复时先人工核实原运行与副作用，另给明确处理指令，不能把这里当自动恢复协议。

状态文件不是命令队列；只消费固定配置的一条决定。启用后按既有原生频率检查，不增加第二调度器、不保证即时唤醒。跨任务/原生重启恢复、工具权限及竞态现场表现仍待单 Issue 试验验证。

## 离线辅助与检查

`python3 -B scripts/adoption_record.py < input.json` 只从stdin读严格JSON并向stdout输出提案，不写文件、不调用GitHub或执行业务。不要将真实配置/记录临时文件提交仓库。初始化和空闲检查使用独立输入，例如：

```json
{"operation":"initialize","record":null,"expectedBlobSHA":null,"observedBlobSHA":null}
```

该输入仅适用于已获准初始化且确认文件不存在；工具只输出提案，不执行写入。已有合法idle时record传实际JSON、两个SHA传当前实际blob SHA，initialize返回write=false；消费者使用相同四字段、operation=inspect_idle，文件必须存在，返回idle_do_not_execute。任何多余配置/Issue/决定/run字段均拒绝。此小结构不更改原有真实记录输入。

非空记录或idle到真实pending的输入使用精确字段：`operation,config,observedIssue,now,nativeActive,record,expectedBlobSHA,observedBlobSHA,runId,decision,result`；observedIssue为repository/number/createdAt/url/state，由实际读取响应映射，不能由槽文件提供；create的decision为decidedAt/sourceRef，producer runId为null；其它操作decision为null，消费者runId必须为真实原生运行标识。首次不存在时record及两个SHA均为null；更新时都使用实际读取的文件与40位blob SHA。只有complete/blocked/unknown携带result，其余为null。

输出 `write` 只是建议是否需要调用文件工具；`nextAction` 明确要求写后读回或核对原任务，永远不是业务执行授权。函数检查配置/Issue/时间匹配、固定位置、状态、同run及旧SHA一致，拒绝未知/重复字段。输入中的nativeActive、配置来源和Issue事实仍须由宿主实际核验，不能把布尔值当认证。

运行 `python3 -B scripts/test_adoption_record.py`；合成数据覆盖合法空槽初始化/复用、拒绝混入身份、空闲到首次采纳的旧SHA转换、禁止非空回空闲，以及首次/重复、历史与未来、配置/Issue/路径伪造、暂停到期、旧SHA冲突、双消费者模拟、重复认领、未知、取消与两个终点。它不验证真实GitHub CAS、账号来源、原生互斥、部署、通知或即时停止。首次真实读写与本地原生加载/认领/取消验证须另获准，保持新单一单，不重放旧采纳。
