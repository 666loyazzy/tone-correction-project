# 验证记录

验证日期：2026-10-14。项目名称：声调纠正系统。

## 已通过

- 14 项 Python 接口/智能体测试：F0、静音、选区时间偏移、WebM/M4A 解码、输入异常、账号隔离、教师授权、CSRF、会话、memory 控制、harness 顺序和模型 prompt。
- 5 项录音状态测试：最后音频块完整性、重复停止、授权取消后迟到流释放、编码回退、权限拒绝与后台中断。
- Chromium 浏览器流程：虚拟麦克风录音、回听、麦克风释放、Praat 提取、八步分析流程、问答、memory 修改、两次练习对比、刷新恢复、手机宽度布局及权限异常。
- Docker 镜像构建；PostgreSQL、Redis、API、Celery worker 与 beat 启动。容器部署中重复执行浏览器录音流程，分析由独立 Celery worker 完成。
- 公开源码检查：不包含模型密钥、数据库、真实录音、上传的论文原文或环境秘密。

合成 200 Hz 信号的提取结果约为 200 Hz；静音不生成有效 F0 或评价特征。它们用于确认接口和时间轴，不作为真实语音准确率证据。

## 待实际配置或语料验证

- 真实 iOS、Android、Safari 与实际麦克风的采样、权限和后台中断行为。
- 学校服务器的域名、TLS 证书与外部访问。
- 实际大模型 API；测试中使用 mock 验证了 prompt、skills 和 memory 的传递。
- 教师参考音、说话者音域校准、语言学音节边界、教学评分和学习者效果。

## Windows 本机验证（2026-10-14）

实际安装目录：`E:\tone-correction-project`。独立 Python 3.12.14 基础运行时、Windows `.venv`、FFmpeg、依赖缓存、运行日志、SQLite 和录音均位于该目录内。未使用 Docker Desktop；没有把源码位置当作 Docker 命名卷位置。

- 本机健康端点通过，监听 `127.0.0.1:8000`，用户访问 `http://localhost:8000`。
- 在 Windows 重新通过 14 项 Python 测试、5 项录音状态测试及前端语法检查。
- 修复应用退出时 SQLite 连接未释放造成 Windows 临时数据库无法清理的问题；重新运行 Python 测试通过。
- Windows PowerShell 5 入口实际通过启动、`.env` 加载、SQLite 备份、进程树停止、端口释放和重启验证。健康检查直接访问 IPv4 loopback 并避开系统代理。
- 使用 Windows Edge 的合成音虚拟麦克风、独立数据目录和测试账号，完成录音、回听、麦克风释放、Praat、八步 harness、图表、问答、memory、复练对比、刷新、手机宽度和拒绝权限检查。
- 另实际停止并重启独立验证服务，同一登录会话仍可读取两次练习、原 F0 数据、memory 和 WAV 音频。
- 正式账号和练习目录为 `data`；验证账号仅位于忽略的 `runtime/browser-validation-*`，不使用正式账号初始化。
- DeepSeek 地址和模型名已配置，API 密钥留空。健康端点 `tutor_api_configured=false`，问答使用本地教学规则；没有完成真实模型 API 联调。

公开仓库地址：[666loyazzy/tone-correction-project](https://github.com/666loyazzy/tone-correction-project)。发布源码时排除 `.env`、`runtime`、`.venv`、`data`、`logs`、`backups` 和浏览器产物；仓库创建与源码推送分别检查实际远程状态。

本次录音验证仍为合成音。真实麦克风、手机设备和学习者效果尚需实际验收。

截图采用虚拟麦克风合成音和演示账号，不包含真实学习者的评价数据。
