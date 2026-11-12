# 远端采集器

目录是唯一登记源。中心运行 python3 scripts/add_collector.py <server-id> https://你的中心域名，生成 runtime/collector-<server-id>.json；令牌不会打印。它只包含该宿主需要的检查目标，不包含其他主机令牌。中心重启 console 后生效。

## Linux 与 NAS

需要 Python 3.7 或更新版本、systemd，Docker 检查需要读取本机 Docker 状态的权限。安装命令：sudo sh collectors/linux/install.sh /绝对路径/config.json。检查 systemctl status nestops-collector。卸载 sudo sh collectors/linux/uninstall.sh；保留私有配置，需要在中心撤销令牌，再按自己的数据清理策略删除配置。

采集器默认 root，因为 Docker 状态读取通常需要该权限；它没有接收命令的端口，只运行配置中允许的本地检查。用户可以自行使用已有最小权限账号，不能把 docker 组当作低权限边界。NAS 非 systemd 或安全标签不同的系统需要适配其原生任务机制，不自动取消系统安全限制。

### 指定 Docker 程序路径

systemd 服务的 PATH 可能与交互终端不同。Linux/NAS 可在本机私有采集配置（config.json）的顶层增量设置：

```json
{
  "docker_binary": "/opt/example/bin/docker"
}
```

该值必须是管理员信任的本地绝对路径，目标须为存在且可执行的文件；采集器使用参数数组直接调用，不通过 shell，也不接受命令参数字符串。显式路径缺失、不可访问、不可执行或 Docker 查询失败时，容器监测显示 `unknown`，不会回退到其他 Docker。未设置时保留 PATH 搜索及原有默认路径检查。该宿主级字段不由目录生成器自动设置，不应把真实 NAS 安装路径写进公开示例。

### 定时备份任务

Linux/NAS 可使用 `backup_job` 观察 systemd 定时备份，或通过显式 `scheduler: "backrest"` 观察本机 Backrest 计划、真实备份操作和持久完整检查回执；macOS 使用 `scheduler: "launchd"` 观察 root 系统计划。只读取本机计划状态、配置及只读操作历史，不执行备份、恢复或仓库查询，不读取备份密码。不要把每日 oneshot service 加入 `units`，正常等待时的 `inactive` 不代表备份故障；真实任务运行且上一份 `read-data` 成功回执仍在 26 小时内时显示 `maintenance`（“备份运行中”）。Linux 回执读取要求普通文件、非符号链接、稳定设备号/inode/大小/mtime/所有者/权限，并限制为 64 KiB；POSIX 还要求 root 所有且权限为 `0644`（macOS 另要求 root:wheel）。运行态回执和 `transport_receipt` 独立读取、独立回传：某一份损坏不会吞掉另一份已知故障，缺失或损坏的已配置 transport 会显式回传 `unknown`。配置和回执格式见 [配置说明](configuration.md#定时备份监测)。

更新现有采集器时保留原令牌，增量同步这项配置和采集脚本；不要为新增监测项重新生成或轮换整台宿主的令牌。macOS 的 plist 必须固定 SHA-256，系统 launchd 计划必须为 root:wheel、每日 04:00、`RunAtLoad=false`，且没有额外触发器；plist、摘要、回执或身份任一不符都显示 `unknown` 或 `down`，不会猜测成功。

## macOS

已有系统 Ruby；Python 仅用于安装脚本。以当前登录用户执行 python3 collectors/macos/install.py /绝对路径/config.json。Docker 路径可在配置 docker 字段调整。检查 launchctl print gui/$(id -u)/org.nestops.collector。卸载运行同目录 uninstall.py。

这是用户 LaunchAgent，依赖用户登录及对应容器运行时，不宣称登录前可采集。没有远程命令执行接口。

## 更新与检查

先验证新脚本，停对应 collector，替换安装目录中的 collector.py 或 collector.rb，再启动；保留 config.json。配置新增检查目标时从中心登记源重新生成检查列表，保留或显式轮换当前令牌。删除检查目标时先更新远端配置再更新中心目录。

备份监测涉及中心与采集器的配套协议。升级已有来源时先部署中心再升级采集器：旧采集器只回传运行态而没有本次安全快照或已配置的 transport 证据时，中心会把备份的 `up`/`maintenance` 降为 `unknown`，不会沿用上一次 `up`。新中心兼容旧采集器把 transport 嵌在 `backup.transport` 的形状，但新采集器发送顶层 `transport`；待中心稳定后再逐台替换采集器并观察一次正常回传。不要先把新采集器与旧中心组合运行，也不要在上线过程中清空历史 SQLite。中心启动时执行幂等迁移，从旧 `probe_notifiable`、事件流和嵌套 transport 重建服务级 incident、当前证据及有界历史。

HTTPS 验证保持开启；不要使用关闭证书验证的方式排错。采集失败不会伪造 up，停止的已登记容器为 down，Docker 不可读取为 unknown，unhealthy 为 degraded。
