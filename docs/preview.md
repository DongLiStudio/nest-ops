# NestOps 界面预览

本页展示公开仓库中的界面样式和交互范围。所有截图都由 `scripts/preview.py` 使用合成数据生成，仅用于说明界面，不代表任何生产环境的服务数量、可用率或告警记录。

## 服务入口

![服务入口视图](assets/gallery/services-demo.jpg)

展示分类导航、服务卡片、状态标记、时间范围和筛选控件。

## 服务器总览

![服务器总览视图](assets/gallery/servers-demo.jpg)

展示多台主机、每台主机的服务数量和异常数量，以及按服务器查看全部服务的入口。

## 可用性列表

![可用性列表视图](assets/gallery/availability-demo.jpg)

展示服务按可用率、监测覆盖率和稳定名称排序后的列表、历史色条、响应时间和分页。

## 告警与故障恢复记录

![告警与故障恢复记录视图](assets/gallery/alerts-demo.jpg)

展示当前异常、故障与恢复事件，以及事件总数和分页控件。

## 数据与隐私口径

- 截图中的主机名和地址是演示用名称与 RFC 5737 文档保留地址。
- 图片不包含真实公网 IP、域名、令牌、Cookie、家庭网络拓扑或生产历史。
- README 中的图表链接到公开 Star History 服务，不上传仓库私有数据。
- 生成本地预览：`python scripts/preview.py`，然后打开 `http://127.0.0.1:18765/`。
