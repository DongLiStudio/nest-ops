# 中心部署与验收

中心 Compose 包含 Homepage、console、status、gateway；可选 oidc profile 包含 Authelia。只有网关发布端口，后端和状态接口仅容器网络访问。无需中心 Docker socket 或 SSH 私钥。

1. 按 README 初始化。用 UID/GID 1000 的部署用户运行初始化；其他 UID 时将 Compose console/status/authelia 的 user 改为对应 UID/GID，或调整其私有目录属主。不要把配置改为所有人可读。
2. 配置 .env 域名、DNS 与 HTTPS 证书。网关 UID/GID 101，需要证书读取权限，例如 root 安装后将 runtime/tls 的证书属主设为 101:101、文件模式 600。runtime 父目录保持 755；不公开私钥。
3. 运行 prepare.py。自定义背景自行放 runtime/images 并配置 settings.yaml；仓库不发布个人照片，默认使用纯色背景。
4. 默认使用 Homepage 原生密码认证。首次上线前读取 runtime/homepage.env 中生成的密码并妥善保管。
5. 如需双因素：使用 examples/homepage-oidc.env 替换相应环境项，删除密码项；在 runtime/auth/config 放置经 Authelia 验证的用户、session、storage、notifier 和 OIDC issuer/client 配置。把 examples/authelia-nginx.conf 中域名改为实际 auth 域名，加入 runtime/nginx.conf 的 http 块。运行 docker compose --profile oidc up -d。不要混用示例占位密钥与现网身份。
6. 在 runtime/console/registry.json 登记自己的服务。运行 add_collector.py 生成每台令牌，安全传输配置并安装。重启 console 读取目录和令牌更新。
7. 可选定制家庭卡片：将状态 agent 部署到 UPS 所在机器，设置 STATUS_URL 为中心 /_home_status/ingest、UPS_URL 为本地 UPS API、STATUS_TOKEN_FILE 为该采样器令牌文件。使用中心生成的 status-token，勿上传 UPS API 自身密钥。

验收：docker compose config --quiet；各容器正常；HTTPS 证书及登录回跳正常；匿名 /_ops/api 为 401；登录后有数据；各主机心跳更新；停采后未知；三个视图、筛选、分页、原生入口与移动端操作正常。配置 SMTP 后分别测试一次故障与恢复，检查收件箱，不把 SMTP 接受视为实际送达。

生产定制登录配置和卡片数据不由通用示例覆盖。此包不自动签发证书；接入已有证书自动更新方案，并在换证后执行 docker compose exec gateway nginx -t 及 nginx -s reload。
