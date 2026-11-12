# 配置与统计口径

## 服务名称与服务器归属

1Panel、Clash、ALLinSSL、宝塔等通用运维工具会自动补充“ · 服务器名称”。不同服务器上的同名服务也自动补充归属；已带归属的名称不重复追加。服务器名称来自目录的 servers，服务 ID、探测配置和历史序列不变。页面、详情和通知共用同一名称，后续新增实例无需单独修改前端。

## 排序与事件分页

全部已登记服务（含单台服务器列表）按所选范围可用率升序、覆盖率升序、中文名称与ID稳定排序，然后每15项分页。可用率为空的放末尾；统计展示不变。

告警页和服务详情使用认证后的`/events`后端分页，每页15条、时间及ID倒序。时间范围与服务/已登记资源过滤在分页前执行，不受快照最新200条限制。首次查询固定时间锚点，翻页时新事件不会挤动已有页面；“查看最新”重置锚点。历史保留期仍为366天。

runtime/console/registry.json 是服务目录唯一来源，结构见 examples/registry.json。每项 service 必须包含唯一 id、server、name、category、app、href、containers、units。app 表示业务应用，是服务的子集；href 非空才展示为可点击入口。系统自带进程不默认登记。Minecraft 等由专门面板管理的子资源无需重复登记。

可选择 probe: {url, codes, label} 做 HTTP/TLS 入口检查；URL 只允许 http/https、必须有主机名且不能内嵌用户名或密码；codes 为非空、不重复的 100–599 HTTP 状态码，label 最长 80 个可打印字符。中心负责 probe；同一服务可再带一个宿主本地目标（containers、units、timer、process 或 backup_job 之一），生成来源采集配置时会保留该本地目标并移除私有 URL。仅 probe 的服务不会分发给来源采集器。目录文件由管理员维护，不能把不可信输入作为探测 URL。units/process/timer/backup_job 为本地允许的检查目标，不是远程命令字符串。

私有入口后缀放 runtime/console/entries.json，映射 service id 到完整 http/https/hometunnel URL，仅认证接口返回。不要把管理路径或 HomeTunnel 主机指纹提交到公开仓库。

可用率 = 已知正常时间 /（正常 + 不可用 + 异常时间）。未知和维护时间不进入这个分母，另以监测覆盖率展示。顶部可用率是各有数据服务的等权平均。新服务不补造历史，累计与时间窗口分开。

历史数据在 runtime/history/history.sqlite；服务身份变化会形成新序列，应保持稳定 id。采集超过 150 秒过期为未知，主机采集失联不等于确认整机停机。

SMTP 可选：复制 examples/notify.json 到 runtime/console/notify.json、填写真实凭据，将 enabled 改为 true 后重启 console（Compose 已挂载 OPS_NOTIFY）。默认关闭通知，避免示例安装误发。恢复通知仅对确实故障后恢复的事件生成。

## 定时备份监测

Linux/NAS 的服务项可设置以下字段；不要同时设置 `containers`、`units`、`timer` 或 `process`。`probe` 可由中心并行执行，但不会进入来源采集配置。业务目录仍可使用 `app: false` 与 `href: ""`，只显示内部监测项。

```json
{
  "backup_job": {
    "timer": "example-backup.timer",
    "service": "example-backup.service",
    "receipt": "/var/lib/example-backup/last-success.json",
    "max_age_seconds": 93600
  }
}
```

`max_age_seconds` 默认为 93600 秒（26 小时），可设为其他正数以匹配实际调度与执行预算。`receipt` 是任务在备份、校验和必要收尾全部成功后原子更新的持久 JSON 文件，不应放在重启会清空的临时目录。采集器只读取它：

```json
{
  "status": "success",
  "profile": "demo-1",
  "snapshot": "example-snapshot",
  "checked": true,
  "check_mode": "read-data",
  "utc": "2026-01-01T02:15:00+00:00"
}
```

### 安全回传协议

采集器以独立主机令牌 `POST /_ops/ingest`。备份服务只能回传 `state` 和可选的 `backup`；普通服务只允许 `state`，且 `maintenance` 保留给备份运行态。服务 ID 必须存在且属于该令牌对应主机，探测型服务由中心负责，不能由采集器回传。顶层只允许 `services` 与 `metrics`；未知服务、跨主机服务、未知服务观测字段、错误类型或超限请求会使整包返回 HTTP 400，且中心在完成整包校验前不写入任何服务状态。`metrics` 只持久化 `load1`、`memory_percent`、`disk_percent`、`uptime_seconds`，其他指标键被过滤。

备份元数据是严格白名单对象：

```json
{
  "schema_version": 1,
  "checked_at": 1767225600.0,
  "snapshot_id": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "check_mode": "read-data",
  "transport": {
    "checked_at": 1767225600.0,
    "state": "up",
    "unauthenticated_status": 401,
    "config_status": "pinned",
    "error_code": null
  }
}
```

根对象只允许上述五个字段，`transport` 可选；出现 `receipt_path`、异常原文、主机地址、令牌或其他字段会整项判为无效。`schema_version` 必须为整数 `1`，`checked_at` 是有限、非负的 Unix 秒且最多领先中心 300 秒；`check_mode` 严格为 `read-data`。`snapshot_id` 为 1–128 个字符，只能以字母或数字开始，并只含字母、数字、点、下划线、冒号和连字符。`transport` 出现时只允许五个字段；`state` 只能是 `up`、`down` 或 `unknown`，未认证状态只能是 `null`/`401`，配置状态只能是 `null`/`pinned`，`error_code` 只能是小写稳定分类。`up` 必须同时具有 401、pinned 且无错误。

中心会保留最后一次有效元数据供页面说明，但不会拿它覆盖本次无效或缺失观测。回传的 `checked_at` 早于已保存值，或时间相同但快照 ID 不同，会被拒绝，旧值也不能回滚新值。`up` 与 `maintenance` 都必须同时得到本次有效、且未超过对应 `max_age_seconds` 的安全快照；缺失或结构无效时运行态降为 `unknown`，结构有效但过期时降为 `down`。上传通路明确为 `down` 时同一次观测的有效备份状态也记录为 `down`。上传通路回执超过 900 秒会在采集器与中心两侧投影为 `down`/`stale`，不会因页面刷新而重新变绿。

回执必须是 JSON 对象，`status` 严格为 `success`，`checked` 严格为布尔值 `true`，`check_mode` 必须是字符串 `read-data`，`profile` 与采集配置的 `server` 完全一致，`snapshot` 是非空字符串，`utc` 是带 UTC 时区的有效 ISO 时间（`+00:00` 或 `Z`）。`checked=false` 表示备份任务明确报告未完成完整检查并显示 `down`；`checked` 为数字、字符串或其他类型，以及 `check_mode` 缺失、为空或非字符串时，表示回执格式无法确认并显示 `unknown`。合法的其他非空 `check_mode` 字符串表示检查不满足全仓读检要求并显示 `down`。采集器不会访问仓库验证快照，回执应由受信任的本机备份任务维护。

- 定时器必须 `active` 且 `enabled`（含 `enabled-runtime`）；停用、停止或失败显示 `down`。
- service 最近一次明确执行失败显示 `down`，即使仍有旧成功回执；正常等待中的 `inactive` 不算故障。正在运行或重试且上一份成功回执仍有效、未超过 26 小时时显示 `maintenance`（“备份运行中”），不计入不可用，也不触发告警；运行中若回执已过期则显示 `down`，回执缺失或损坏则显示 `unknown`。
- 有效且未过期的回执显示 `up`，超过期限显示 `down`。宿主重启后 systemd 的执行时间归零不抹去持久成功证据。
- 回执缺失、损坏、字段不符、时间无时区或非 UTC、时间超前超过 300 秒显示 `unknown`；systemd 查询失败或缺少必要属性也显示 `unknown`。已明确的停用或失败优先显示 `down`。

service 的 `Result`/`ExecMainStatus` 是 systemd 的当前观测，宿主重启后可能重置。此时只能结合仍保留的成功回执及其时效判断，重启前较新的失败可能无法重建；本监测不持久化最近失败，也不会写回备份任务的状态文件。`up` 不能证明最近一次失败跨重启仍可追溯。

这些状态表示定时任务和成功回执的观测结果；实际恢复与异地副本需要独立验收。已有 `timer` 字段保持原有“读取 service 最近执行结果”的行为，不自动升级为 `backup_job`。

### 本机 Backrest 计划

使用本机 Backrest 执行备份时，可显式设置 `scheduler: "backrest"`。以下为配置示例；路径和计划ID须按本机现行配置填写，`server` 必须与 Backrest 的 `instance` 一致。

```json
{
  "backup_job": {
    "scheduler": "backrest",
    "service": "example-backrest.service",
    "config": "/opt/example-backrest/config/config.json",
    "oplog": "/var/lib/example-backrest/oplog.sqlite",
    "plan": "example-daily",
    "receipt": "/var/lib/example-backup/last-success.json",
    "max_age_seconds": 93600
  }
}
```

该适配按 Backrest v1.14.1 的配置和 SQLite 操作历史格式验证；升级 Backrest 后应先复验格式。采集器以只读方式打开操作历史，仅接受当前本机实例、指定计划和仓库 GUID 的真实 `OperationBackup`。操作历史按 256 条分页顺序扫描，不会因一页较新的索引、钩子或 dry run 遮挡后面的真实备份；远端同步记录、索引、钩子、待执行操作和 dry run 都不能作为成功备份证据。

- Backrest service 必须活动且已启用；计划必须有启用的 cron 日程，停用服务或计划显示 `down`。
- 最新真实备份失败或取消显示 `down`，警告显示 `degraded`，不会被较新的索引、钩子或 dry run 掩盖。执行中的备份在上一份已完成备份及其完整检查回执仍有效、未超过 26 小时时显示 `maintenance`；回执过期显示 `down`，缺失或损坏显示 `unknown`。
- `up` 必须同时满足真实备份成功、上述回执格式有效、回执快照与该次操作完全一致且未过期；过期显示 `down`，缺失/损坏配置、数据库、操作或回执及身份不符显示 `unknown`。
- 回执由受信任的备份任务在完整检查和收尾成功后维护。Backrest 操作成功本身不代表已经完成仓库完整检查；此适配不会执行检查或写入 Backrest 数据库，也不需要 API 登录密码。

未设置该 scheduler 的现有 systemd 定时器监测保持原行为。中心 Backrest 的界面汇总及远端状态同步应另行验收，不能用本机 service 活动代替。

### macOS launchd 计划

macOS 使用 root-owned 的系统 LaunchDaemon，并固定完整 plist 摘要：

```json
{
  "backup_job": {
    "scheduler": "launchd",
    "label": "com.example.backup",
    "plist": "/Library/LaunchDaemons/com.example.backup.plist",
    "plist_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "receipt": "/Library/Application Support/Example/health.json",
    "transport_receipt": "/Library/Application Support/Example/transport.json",
    "max_age_seconds": 93600
  }
}
```

采集器要求 plist 为 `root:wheel`、摘要与配置一致，`ProgramArguments` 和绝对 `WorkingDirectory` 有效，`StartCalendarInterval` 精确为每日 04:00，`RunAtLoad`、`KeepAlive` 和额外触发器均未启用。任务运行且上一份成功回执仍有效时显示 `maintenance`；结束后仍按真实退出状态、快照、`checked=true`、`check_mode=read-data` 和 26 小时时效判断。plist 内容或摘要变化时状态为 `unknown`，必须重新审阅并显式更新配置摘要。

### 通知边界

备份运行中的 `maintenance` 和未能确认的 `unknown` 会写入历史与页面，但不发送异常邮件；中性状态也不会清除此前已经确认的故障。若来源从 `down`/`degraded` 进入 `maintenance` 或 `unknown` 后再次异常，不重复发送同一事故；之后真正恢复为 `up` 时仍发送一次恢复邮件。没有此前的可通知故障时，`up` 不发送孤立恢复邮件。上传通路 `down` 使用同一 `down` 事故语义，恢复后发送一次恢复邮件。

## HTTP 探测重试与告警防抖

每轮入口探测最多两次，单次超时6秒，失败间隔1秒后重试；任一次成功即本轮为正常。第一、二轮均失败显示黄色 `degraded`（波动），第三轮仍失败才显示红色 `down`（确认不可用）。只有确认故障发送一次异常邮件，确认后的首次成功发送一次恢复邮件；恢复于确认前则不发送异常或孤立恢复邮件。轮次间隔超过150秒时重置未确认的失败计数，防止采集暂停积累成连续故障；计数和确认状态保存在SQLite，正常重启不会凭空清空已确认事件。

每次底层尝试都写入独立 `probe_attempts` 表并保留31天，含时间、尝试序号、状态、耗时及错误分类；主历史/事件只写入每轮聚合状态，避免同一轮两次尝试造成红绿闪烁。可用率按聚合轮次计算：前两轮失败为黄色异常，第三轮起为红色不可用。DNS、timeout、TLS证书/握手、连接拒绝/重置分别分类；不保存异常原文、请求URL、头或私有路径。单阶段耗时尚不提供，记录的是该次请求总耗时；同步DNS解析也不保证受socket超时严格限时。

宿主采集与主机失联通知保持原逻辑。升级后HTTP探测历史继续保留，尚未发送且未经连续失败确认的旧原始转换不追发邮件；已发送记录不删除。通知渠道失败仍保留 `failed` 并最多重试5次、间隔至少5分钟；数据库只记录脱敏错误类别（`dns`、`timeout`、`tls`、`auth`、`connection`、`send`），不保存服务器异常原文、地址或凭据。

家庭定制接口独立保留，未来照片、视频、音乐、图书统计可按实际数据源接入，不建立多余的统一协议。
