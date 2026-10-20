# 配置与统计口径

## 服务名称与服务器归属

1Panel、Clash、ALLinSSL、宝塔等通用运维工具会自动补充“ · 服务器名称”。不同服务器上的同名服务也自动补充归属；已带归属的名称不重复追加。服务器名称来自目录的 servers，服务 ID、探测配置和历史序列不变。页面、详情和通知共用同一名称，后续新增实例无需单独修改前端。

## 排序与事件分页

全部已登记服务（含单台服务器列表）按所选范围可用率升序、覆盖率升序、中文名称与ID稳定排序，然后每15项分页。可用率为空的放末尾；统计展示不变。

告警页和服务详情使用认证后的`/events`后端分页，每页15条、时间及ID倒序。时间范围与服务/已登记资源过滤在分页前执行，不受快照最新200条限制。首次查询固定时间锚点，翻页时新事件不会挤动已有页面；“查看最新”重置锚点。历史保留期仍为366天。

runtime/console/registry.json 是服务目录唯一来源，结构见 examples/registry.json。每项 service 必须包含唯一 id、server、name、category、app、href、containers、units。app 表示业务应用，是服务的子集；href 非空才展示为可点击入口。系统自带进程不默认登记。Minecraft 等由专门面板管理的子资源无需重复登记。

可选择 probe: {url, codes, label} 做 HTTP/TLS 入口检查；无 probe 的登记服务接收宿主采集。目录文件由管理员维护，不能把不可信输入作为探测 URL。units/process/timer/backup_job 为本地允许的检查目标，不是远程命令字符串。

私有入口后缀放 runtime/console/entries.json，映射 service id 到完整 http/https/hometunnel URL，仅认证接口返回。不要把管理路径或 HomeTunnel 主机指纹提交到公开仓库。

可用率 = 已知正常时间 /（正常 + 不可用 + 异常时间）。未知和维护时间不进入这个分母，另以监测覆盖率展示。顶部可用率是各有数据服务的等权平均。新服务不补造历史，累计与时间窗口分开。

历史数据在 runtime/history/history.sqlite；服务身份变化会形成新序列，应保持稳定 id。采集超过 150 秒过期为未知，主机采集失联不等于确认整机停机。

SMTP 可选：复制 examples/notify.json 到 runtime/console/notify.json、填写真实凭据，将 enabled 改为 true 后重启 console（Compose 已挂载 OPS_NOTIFY）。默认关闭通知，避免示例安装误发。恢复通知仅对确实故障后恢复的事件生成。

## 定时备份监测

Linux/NAS 的服务项可设置以下字段；`containers`、`units` 留空，不设置 `probe`。业务目录仍可使用 `app: false` 与 `href: ""`，只显示内部监测项。

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
  "profile": "demo-1",
  "snapshot": "example-snapshot",
  "checked": true,
  "utc": "2026-01-01T02:15:00+00:00"
}
```

回执必须是 JSON 对象，`checked` 严格为 `true`，`profile` 与采集配置的 `server` 完全一致，`snapshot` 是非空字符串，`utc` 是带 UTC 时区的有效 ISO 时间（`+00:00` 或 `Z`）。采集器不会访问仓库验证快照，回执应由受信任的本机备份任务维护。

- 定时器必须 `active` 且 `enabled`（含 `enabled-runtime`）；停用、停止或失败显示 `down`。
- service 最近一次明确执行失败显示 `down`，即使仍有旧成功回执；正常等待中的 `inactive` 不算故障。正在运行或重试时依回执判断，避免把保留的上次失败结果当作本次失败。
- 有效且未过期的回执显示 `up`，超过期限显示 `degraded`。宿主重启后 systemd 的执行时间归零不抹去持久成功证据。
- 回执缺失、损坏、字段不符、时间无时区或非 UTC、时间超前超过 300 秒显示 `unknown`；systemd 查询失败或缺少必要属性也显示 `unknown`。已明确的停用或失败优先显示 `down`。

service 的 `Result`/`ExecMainStatus` 是 systemd 的当前观测，宿主重启后可能重置。此时只能结合仍保留的成功回执及其时效判断，重启前较新的失败可能无法重建；本监测不持久化最近失败，也不会写回备份任务的状态文件。`up` 不能证明最近一次失败跨重启仍可追溯。

这些状态表示定时任务和成功回执的观测结果；实际恢复与异地副本需要独立验收。已有 `timer` 字段保持原有“读取 service 最近执行结果”的行为，不自动升级为 `backup_job`。

## HTTP 探测重试与告警防抖

每轮入口探测最多两次，单次超时6秒，失败间隔1秒后重试；成功立即停止重试。连续三轮（包含重试）全部失败，才允许该故障事件发送邮件；此前恢复的短暂波动不发故障或孤立恢复邮件。确认故障后的首次成功允许一次恢复通知。轮次间隔超过150秒时重置未确认的失败计数，防止采集暂停积累成连续故障；计数和确认状态保存在SQLite，正常重启不会凭空清空已确认事件。

告警门槛只影响通知，不延迟或改写原始观测。每次失败和重试成功仍写入历史/事件，独立 `probe_attempts` 表保留31天，含时间、尝试序号、状态、耗时及错误分类。可用率仍按原始观测时段计算，不因重试成功抹去失败。DNS、timeout、TLS证书/握手、连接拒绝/重置分别分类；不保存异常原文、请求URL、头或私有路径。单阶段耗时尚不提供，记录的是该次请求总耗时；同步DNS解析也不保证受socket超时严格限时。

宿主采集与主机失联通知保持原逻辑。升级后HTTP探测历史继续保留，尚未发送且未经连续失败确认的旧原始转换不追发邮件；已发送记录不删除。通知渠道失败重试最多5次、间隔至少5分钟的原机制保持。

家庭定制接口独立保留，未来照片、视频、音乐、图书统计可按实际数据源接入，不建立多余的统一协议。
