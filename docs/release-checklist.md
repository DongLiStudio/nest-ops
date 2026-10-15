# 发布门禁

公开前必须完成：

- [ ] 同步经现网验证的 homepage/custom.js、custom.css 与 console/ui.js、ui.css，移除所有私有域名、IP、指纹、文档链接和管理路径。
- [ ] python3 scripts/verify.py 全部通过（包含 1525 个上游固定文件的集合与 SHA256）。
- [ ] python3 console/tests.py、python3 scripts/test_collectors.py 通过。
- [ ] 使用示例域名初始化私有 runtime，docker compose config --quiet 通过；检查 secrets 不进入版本控制。
- [ ] 在隔离测试环境真实启动，匿名接口401、认证回跳、首屏、三视图、详情、筛选、分页、通知及远端采集验收。
- [ ] 二次人工敏感信息检查；Git 仅包含通用源码、示例与许可证。
- [ ] 记录公开仓库提交、CI 和实际测试范围；不把静态校验称为完整部署验收。

2026-10-15 封装阶段离线证据：后端10项、采集3项回归通过，init/prepare 临时目录 fixture 通过；Homepage固定归档1525文件逐字节一致。四个前端文件尚待主任务同步，公开包未在独立容器环境启动，不声明端到端通过。
