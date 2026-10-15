# 远端采集器

目录是唯一登记源。中心运行 python3 scripts/add_collector.py <server-id> https://你的中心域名，生成 runtime/collector-<server-id>.json；令牌不会打印。它只包含该宿主需要的检查目标，不包含其他主机令牌。中心重启 console 后生效。

## Linux 与 NAS

需要 Python 3、systemd，Docker 检查需要读取本机 Docker 状态的权限。安装命令：sudo sh collectors/linux/install.sh /绝对路径/config.json。检查 systemctl status nestops-collector。卸载 sudo sh collectors/linux/uninstall.sh；保留私有配置，需要在中心撤销令牌，再按自己的数据清理策略删除配置。

采集器默认 root，因为 Docker 状态读取通常需要该权限；它没有接收命令的端口，只运行配置中允许的本地检查。用户可以自行使用已有最小权限账号，不能把 docker 组当作低权限边界。NAS 非 systemd 或安全标签不同的系统需要适配其原生任务机制，不自动取消系统安全限制。

## macOS

已有系统 Ruby；Python 仅用于安装脚本。以当前登录用户执行 python3 collectors/macos/install.py /绝对路径/config.json。Docker 路径可在配置 docker 字段调整。检查 launchctl print gui/$(id -u)/org.nestops.collector。卸载运行同目录 uninstall.py。

这是用户 LaunchAgent，依赖用户登录及对应容器运行时，不宣称登录前可采集。没有远程命令执行接口。

## 更新与检查

先验证新脚本，停对应 collector，替换安装目录中的 collector.py 或 collector.rb，再启动；保留 config.json。配置新增检查目标时从中心登记源重新生成检查列表，保留或显式轮换当前令牌。删除检查目标时先更新远端配置再更新中心目录。

HTTPS 验证保持开启；不要使用关闭证书验证的方式排错。采集失败不会伪造 up，停止的已登记容器为 down，Docker 不可读取为 unknown，unhealthy 为 degraded。
