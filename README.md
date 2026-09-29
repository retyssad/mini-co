# mini-co

mini-co 是一个可以读代码、修改文件和运行命令的本地编程 Agent。项目使用 Python 编写，提供 PySide6 单窗口桌面界面，也保留命令行入口。桌面版包含会话列表、文件和改动查看、任务状态、计划模式，以及模型、MCP 和 hooks 设置。

> 这是一个便于阅读和改造的项目。运行 Agent 前，请先确认工作目录和模型服务；涉及写文件或执行命令的操作会请求授权。

## 安装与启动

需要 Python 3.10 或更新版本。在项目根目录运行：

```bash
python -m pip install -e ".[gui]"
mini-co-gui
```

Windows PowerShell 如果找不到 `mini-co-gui`，可以通过模块启动：

```powershell
python -m mini_co.gui
```

第一次发送消息前，在右上角的模型设置中填写 API Key、模型名称和 API 地址。你也可以在启动前设置环境变量，或在项目根目录创建不会提交到 Git 的 `.env` 文件：

```dotenv
OPENAI_API_KEY=your-key
MINI_CO_MODEL=your-model
OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
```

默认模型名称为 `gpt-5.5`。使用其他兼容 OpenAI API 的服务时，应同时修改模型名称和地址。也支持 `MINI_CO_API_KEY`、`MINI_CO_BASE_URL`；可用 `MINI_CO_PROVIDER=litellm` 切换到可选的 LiteLLM 后端，先安装 `python -m pip install -e ".[gui,litellm]"`。

## 桌面界面

- 左侧新建、打开或删除已保存的会话。删除前会弹出确认框。
- 顶部选择工作项目、打开模型与集成设置。
- 中间发送任务并查看回答、工具调用和授权请求；`Ctrl+Enter` 可发送。
- 计划模式先让 Agent 调查并提出方案，点击“批准执行”后继续。
- 右侧查看项目文件、改动内容和任务状态；可以撤销本次修改。

会话文件保存在 `~/.mini-co/sessions/`。MCP 服务和 hooks 的 JSON 配置分别保存在 `~/.mini-co/mcp.json` 与 `~/.mini-co/hooks.json`，也可以在界面设置中编辑。MCP 使用 stdio 服务；hooks 支持 `PreToolUse` 和 `PostToolUse`。

## 命令行

只需要命令行时，可安装 `python -m pip install -e .`，然后运行：

```bash
mini-co                 # 交互模式
mini-co --demo          # 无需 API Key 的离线演示
mini-co -p "检查这个项目的测试"  # 单次任务
mini-co --help          # 查看完整参数
```

交互模式支持 `/plan`、`/save`、`/sessions` 等命令。单次任务默认不会批准修改文件或运行命令；只有明确传入 `--yes` 才会自动批准工具调用，请仅在可信工作目录使用。

## 开发

```bash
python -m pip install -e ".[gui,dev]"
python -m pytest -q
ruff check mini_co tests
```

核心代码位于 `mini_co/`，桌面界面位于 `mini_co/gui.py`，测试位于 `tests/`。想了解 Agent 循环、工具、上下文和扩展机制，可以阅读[中文源码导读](article/00-index.md)。

许可证：[MIT](LICENSE)。
