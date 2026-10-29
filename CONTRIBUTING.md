# 贡献指南

感谢帮助改进这个本地工具。开始修改前，请阅读 [README](README.md)、[开发指南](docs/development.md) 和 [AGENTS.md](AGENTS.md)。

## 反馈问题

可以在 [GitHub Issues](https://github.com/Kanghz87/wxchat-analysis/issues) 中描述问题，请提供：

- Windows、Python、电脑版微信版本。
- 使用主网页还是备用 Streamlit 界面。
- 操作步骤、预期结果与实际结果。
- 脱敏后的错误提示，或能复现问题的合成数据。

不要提交真实数据库、完整 key、联系人名单、聊天正文或未脱敏日志。截图也应使用演示数据。潜在隐私或安全问题见 [SECURITY.md](SECURITY.md)。

## 修改约定

- 保持功能围绕本机数据、隔离副本和基础统计；较大范围的新功能先说明需求与范围。
- 延续现有 `core → analysis → web` 分层。备用 Streamlit 代码集中在 `legacy/streamlit/`。
- 不因风格调整改变已验证的 key 提取 / 解密算法，不直接操作微信原库。
- 不随意增加依赖或前端构建工具。代码编码、缩进和换行遵循仓库配置。
- 对外行为和目录变化应同步使用 / 开发文档，并为重要行为选择适当的合成数据测试。

## 提交前验证

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m unittest discover -s tests -v
node tests/test_frontend.cjs
git diff --check
git status --short
```

纯文档改动检查链接、命令与目录是否正确即可。没有 Node.js 时，请说明前端状态测试未运行；运行产品本身不需要 Node.js。

备用 Streamlit 界面的测试需要先安装 `requirements-legacy.txt`；只安装主网页依赖时，`tests/test_legacy_ui.py` 会自动跳过。

提交或 PR 说明应包含解决的问题、最终行为和已运行的验证。保持提交内容集中；不要提交 `workspace/`、虚拟环境、数据库、密钥或本机设置，不要通过强制添加绕过 `.gitignore`。
