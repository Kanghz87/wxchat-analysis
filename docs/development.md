# 开发指南

[返回项目首页](../README.md) · [开发助手约定](../AGENTS.md) · [贡献指南](../CONTRIBUTING.md)

## 开发环境

以 Windows 11、Python 3.12 64 位为目标环境。微信 **4.1.13.65** 是已验证版本，其他版本不要标为兼容。

在仓库根目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 web_app.py
```

主网页只依赖 `requirements.txt`。仅在运行备用 Streamlit 界面或 `tests/test_legacy_ui.py` 时，再安装 `requirements-legacy.txt`；未安装时该测试会自动跳过。

手动访问 `http://127.0.0.1:8501`。只有需要自动打开浏览器时才加 `--open-browser`。开发测试不需要 Node.js 构建前端；Node.js 仅用于可选的 JavaScript 状态回归测试。

所有命令从包含 `web_app.py` 的项目根目录运行，代码不依赖开发者机器上的固定盘符。运行时私密文件位于 `workspace/`，不要移动到源码、文档或测试目录。

## 目录与职责

| 路径 | 职责 |
| --- | --- |
| `web_app.py` | 命令行参数、仅本机监听的 Waitress 服务、打开或复用网页 |
| `启动微信分析.cmd` | 使用项目 `.venv` 的 Windows 启动入口 |
| `core/paths.py`、`core/version.py` | 数据路径识别、分片扫描、运行中微信版本识别 |
| `core/workspace.py`、`core/refresh.py` | 隔离复制、元数据、密钥复用、工作区切换 |
| `core/key_extract.py`、`core/decrypt.py` | 已验证的只读提取、候选验证与数据库解密 |
| `core/contacts.py`、`core/messages.py` | 联系人、逐分片发送者映射、消息 DataFrame |
| `core/text.py`、`core/message_types.py` | 文字容器、zstd、消息类型及引用回复正文 |
| `core/sqlite_utils.py`、`core/logging_utils.py` | 副本只读查询、脱敏异常日志 |
| `analysis/basic.py`、`analysis/text_stats.py` | 统计、累计曲线、高活跃区间与全历史文字统计 |
| `web/server.py` | Flask 本地接口和请求边界 |
| `web/service.py`、`web/tasks.py` | 现有缓存、初始化与更新任务协调 |
| `web/data.py`、`web/preferences.py` | DataFrame 转网页数据、本机偏好保存 |
| `web/templates/`、`web/static/` | HTML、CSS、原生 JavaScript 和 Plotly 图表 |
| `legacy/streamlit/` | 保留的备用界面；不承载主网页功能 |
| `tests/` | 合成数据库、接口、图表状态和兼容界面测试 |
| `docs/` | 使用说明、开发说明与合成演示截图 |

核心层不依赖 Flask 或 Streamlit；统计层接收 DataFrame，UI 负责交互与展示。改动优先放到已有模块，不为简单功能引入额外服务、框架或缓存系统。

## 数据流

```text
微信原始目录（只读）
  → 扫描 contact.db 与全部 message_*.db
  → 复制到 workspace/pending/raw（包含已有 WAL/SHM）
  → 逐库验证 key，必要时只读提取
  → 解密到 pending/decrypted，逐库 integrity_check
  → 检查通过后替换 active，最多保留一个 previous
  → 只读查询副本 → 联系人消息 DataFrame → 统计 → 本地网页
```

原始目录永远不做 SQLite 查询、写入、重命名或删除。WAL/SHM 仅复制保留，当前只分析主 DB。启动时如果文件签名与 `validation.json` 一致，只做快速检查；文件变化或更新后仍会逐库重算哈希并执行完整性检查。该缓存只比较路径、大小和修改时间，用于减少重复读盘，不是防篡改机制；删除 `active/metadata/validation.json` 或重新初始化即可强制完整校验。

工作区在首次操作时自动生成，不属于仓库内容：

```text
workspace/
├─ active/
│  ├─ raw/contact/         # 原始加密副本
│  ├─ raw/message/         # 全部分片及已有 WAL/SHM
│  ├─ decrypted/           # 标准 SQLite 明文副本
│  └─ metadata/            # manifest.json、keys.json（敏感）、validation.json（校验签名）
├─ pending/                # 初始化 / 更新的临时槽位
├─ previous/               # 最多一个上一版
├─ preferences.json        # 私密的本机偏好
└─ logs/app.log            # 不含 key 或正文的异常调用栈
```

保留 `owned()` 的路径边界及失败回退。`open_readonly()` 仅用于已解密且不再变化的工作区主库，不应用到微信原始目录。

## 需要保持的行为

- 每个数据库独立验证 32-byte / 48-byte key；48-byte 的后 16 字节为 explicit salt。解密后的 SQLite header 与完整性检查必须正确。
- 扫描全部分片，通过 `Msg_<MD5(username)>` 定位会话；先检查实际 schema，不假设所有库字段一致。
- `real_sender_id` 使用各分片 `Name2Id` 映射，不能把固定编号当作本人。无法确认的发送者不能归给对方。
- 所有聚合来自同一消息 DataFrame；累计曲线单调不减，日期范围只裁切显示，不重置历史累计。
- 全历史文字统计与日期 / 缩放解耦；过滤拍一拍、系统和非文字消息，引用只计本次回复。缓存沿用联系人与当前数据版本，不另建持久化分词系统。
- 四张图的独立日期、全选、跟随最新及偏好记忆保持一致。消息提示使用完整整数，不显示 `k` 缩写。

具体产品口径见 [使用指南](usage.md)。

## 测试与检查

Python 回归使用合成数据，不需要登录微信或提供真实数据库：

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m unittest discover -s tests -v
```

可按改动选择相关模块，例如：

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m unittest tests.test_core tests.test_web -v
```

前端状态测试只用 Node.js 内置模块，无 npm 安装步骤：

```powershell
node tests/test_frontend.cjs
```

启动参数检查，不开启浏览器或服务：

```powershell
.\.venv\Scripts\python.exe -X utf8 -B web_app.py --help
```

提交前检查 `git status --short`、`git diff --stat` 和 `git diff --check`。确认源码、文档和必要测试构成提交，不包含真实数据、环境和日志。测试通过后，不必为了小改动扩张异常矩阵或反复运行相同检查。

`.editorconfig` 约定 UTF-8、空格缩进和文本格式；`.gitattributes` 固定普通文本 LF、Windows 启动文件 CRLF。没有额外的格式化器或前端构建链。

## 备用 Streamlit 界面

实现收在 `legacy/streamlit/`，根目录 `app.py` 只保留兼容启动：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

`.streamlit/config.toml` 保留本机绑定与关闭使用统计的配置。备用界面使用相同的 `core/`、`analysis/` 和 `workspace/`，但不包含主网页后续新增的全部功能。初始化或更新数据库时只运行一个入口。

兼容测试位于 `tests/test_legacy_ui.py`。修改目录时同时检查 Python 导入及 `hourly_chart.html` 的相邻资源路径。
