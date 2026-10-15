# 栖云 NestOps

**你的服务入口与运行全景。**

基于 [Homepage](https://github.com/gethomepage/homepage) 的轻量运维门户，统一服务导航、服务器资产、可用性监测与异常告警。保留官方 Homepage，使用独立 JS/CSS 扩展，中心端由一份 Compose 管理。

## 能力

- Liquid Glass 服务入口、服务器目录、告警三个视图；筛选、分页、服务详情。
- 固定登记目录：停服和失联仍展示，系统进程默认不登记。
- HTTP 入口检查与宿主只读回传、可用性历史条、响应曲线、事件与恢复记录。
- 原生 Homepage 密码或 OIDC 登录；监测接口验证同一登录会话，每台采集器独立令牌。
- 可选家庭 UPS/DDNS 状态卡片接口；其他定制数据按实际需求接入，不强制插件协议。
- Linux/NAS Python 和 macOS Ruby 采集器；无远程命令执行接口，无中心 Docker socket。

## 安装

需要 Linux 中心服务器、Docker Engine 与 Compose v2、Python 3、可用 HTTPS 域名及证书。仓库不含私人配置。

```sh
cp .env.example .env
# 编辑 .env 中的域名
python3 scripts/init.py
# 编辑 runtime/console/registry.json；按文档配置每台采集器
# 把证书放到 runtime/tls/fullchain.pem 与 privkey.pem
python3 scripts/prepare.py
docker compose config --quiet
docker compose up -d
docker compose ps
```

初始化生成的登录密码只写入 runtime/homepage.env；不要提交 runtime。登录 https://你的域名 后查看主页。默认示例服务需改成真实配置，未接入时显示未知。

详见 [部署](docs/deployment.md)、[采集器](docs/collectors.md)、[配置与统计口径](docs/configuration.md)、[升级与恢复](docs/maintenance.md)。原生 HomeTunnel 链接是可选集成，需要另外安装对应客户端。

## 验证与开发

```sh
python3 scripts/verify.py
python3 console/tests.py
python3 scripts/test_collectors.py
```

这些是源码、脱敏与离线回归检查；不替代实际 Docker 启动、认证、TLS、浏览器交互和通知送达验收。首次部署应按部署文档逐项检查。

## 来源与许可

NestOps 以 GPL-3.0 发布。内附固定版本 Homepage 源码与完整许可证，参见 [UPSTREAM.md](UPSTREAM.md)。Homepage、Authelia、Nginx、Python 及其他第三方组件各自的许可证和署名保持不变。

中心服务只提供观察和入口；网页重启、更新和远程命令未实现。监测开始前没有历史数据，未知不计作正常，运行状态不等同于完整业务验收。
