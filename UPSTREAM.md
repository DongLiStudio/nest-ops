# Homepage 上游来源

- 项目：https://github.com/gethomepage/homepage
- 版本：v2.5.0
- 固定提交：347d34c08c4441857b1ff3f9a0cc30e946411d4b
- 许可证：GNU GPL v3，见 vendor/homepage/LICENSE
- vendor/homepage 来自该提交的 git archive，不包含 .git、构建产物或部署配置。

NestOps 使用官方镜像和自定义 JS/CSS。内附源码用于完整留存、审计和自主构建，不会自动把扩展合并进 Homepage。离线构建仍需事先准备镜像和依赖缓存。

更新时先导出指定上游提交，核验归档文件哈希，运行 NestOps 回归和真实浏览器验收，再更新镜像摘要及本文件。禁止直接复制带本地修改的上游工作树。
