# 配置与统计口径

## 排序与事件分页

全部已登记服务（含单台服务器列表）按所选范围可用率升序、覆盖率升序、中文名称与ID稳定排序，然后每15项分页。可用率为空的放末尾；统计展示不变。

告警页和服务详情使用认证后的`/events`后端分页，每页15条、时间及ID倒序。时间范围与服务/已登记资源过滤在分页前执行，不受快照最新200条限制。首次查询固定时间锚点，翻页时新事件不会挤动已有页面；“查看最新”重置锚点。历史保留期仍为366天。

runtime/console/registry.json 是服务目录唯一来源，结构见 examples/registry.json。每项 service 必须包含唯一 id、server、name、category、app、href、containers、units。app 表示业务应用，是服务的子集；href 非空才展示为可点击入口。系统自带进程不默认登记。Minecraft 等由专门面板管理的子资源无需重复登记。

可选择 probe: {url, codes, label} 做 HTTP/TLS 入口检查；无 probe 的登记服务接收宿主采集。目录文件由管理员维护，不能把不可信输入作为探测 URL。units/process/timer 为本地允许的检查目标，不是远程命令字符串。

私有入口后缀放 runtime/console/entries.json，映射 service id 到完整 http/https/hometunnel URL，仅认证接口返回。不要把管理路径或 HomeTunnel 主机指纹提交到公开仓库。

可用率 = 已知正常时间 /（正常 + 不可用 + 异常时间）。未知和维护时间不进入这个分母，另以监测覆盖率展示。顶部可用率是各有数据服务的等权平均。新服务不补造历史，累计与时间窗口分开。

历史数据在 runtime/history/history.sqlite；服务身份变化会形成新序列，应保持稳定 id。采集超过 150 秒过期为未知，主机采集失联不等于确认整机停机。

SMTP 可选：复制 examples/notify.json 到 runtime/console/notify.json、填写真实凭据，将 enabled 改为 true 后重启 console（Compose 已挂载 OPS_NOTIFY）。默认关闭通知，避免示例安装误发。恢复通知仅对确实故障后恢复的事件生成。

家庭定制接口独立保留，未来照片、视频、音乐、图书统计可按实际数据源接入，不建立多余的统一协议。
