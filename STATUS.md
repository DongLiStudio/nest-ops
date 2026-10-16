# Status

控制台性能优化已在源码实现：家庭状态独立认证请求、主快照短时修订缓存、服务曲线按需加载、gzip、最新状态索引、增量 DOM 更新与请求竞态保护。21 项后端回归、采集器及前端加载/分页回归通过；真实浏览器 DOM 复用 8 项断言通过。合成基准及边界见 docs/performance.md。生产部署与线上验收需单独确认，不代表已发布到运行环境。

README 的阅读路径已调整为“界面预览 → 快速开始 → 架构与边界 → 验证与贡献 → 相关项目 → 许可 → Star History”，避免 Star History 打断产品说明。四张合成预览图已统一去除右侧浏览器滚动条，告警图改用无底部遮挡的原始合成画面；未涉及运行时代码或生产配置。

Event panels retain their DOM and loaded records across tab changes and background refreshes. First pages are prefetched and seeded from the current authenticated snapshot; empty first loads use matching event skeleton rows. Pagination failures retain existing data, and request cancellation/sequence checks prevent late responses from overwriting the selected page. Delayed, duplicate, failed and out-of-order response regression tests cover these paths.

Service lists now sort by selected-window availability, coverage and stable name/ID before pagination. Authenticated event pagination queries the retained database with time/resource filtering and a fixed browsing anchor; alerts and detail views use 15-record pages. Existing statistics and visual language are preserved. Synthetic desktop pagination and 390px viewport checks passed; production rollout and CI status must be verified separately.

README now follows the HomeTunnel-style product layout with brand header, badges, quick navigation, synthetic preview, architecture, contribution guidance and related projects. No production inventory or screenshot is included.

README now includes four synthetic gallery screenshots (services, servers, availability and alerts) plus a linked Star History chart. Screenshot metadata and privacy boundaries are documented in docs/preview.md; no production inventory or credentials are included.

NestOps owns Homepage, console and the optional household relay in compose.yml. Authentication and the shared gateway are separate Compose projects, with optional deployment examples in compose.auth.yml and compose.gateway.yml. Existing OIDC providers can be reused.

The private deployment has been split without changing identity data, application code or UI. Public examples use an explicit external network. Source provenance and offline regression tests are validated; fresh generic installation, reboot recovery and notification inbox delivery remain environment-specific acceptance work.
