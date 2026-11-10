# CodeMate 个人编程助手
项目目录：E:\CodeMate

## 运行
双击 start.bat，访问 http://127.0.0.1:8000。停止服务按 Ctrl+C。
已在本机安装独立运行环境；项目、数据和环境均在 E 盘。
首次体验：上传 examples 内的两个文件，点击「定位问题」并发送。
新电脑需 Python 3.11–3.13；运行 start.bat 自动创建虚拟环境并安装依赖。
复制 .env.example 为 .env，填 DEEPSEEK_API_KEY。当前工作副本已配置用户最新提供的 key。
.env 不进入 Git 和源码压缩包。修改配置后重启。
模型默认 deepseek-flash，可改 DEEPSEEK_MODEL；接口地址可改 DEEPSEEK_BASE_URL。

## 图片要求的实现
1. 代码检索：Python AST 按顶层函数/类切分；长函数与其他文本以70行窗口、10行重叠切分；保留文件名、行号。SQLite 持久化原文与向量，Python 精确余弦召回。
2. RAG：使用 LangChain Embeddings 和 ChatOpenAI 接口，将召回片段与问题组合，生成可引用到文件行号的回答。
3. Agent：LangGraph 共享状态与节点：需求分析 → 检索 → 可选工具 → 方案生成。模型规划检索词、简短步骤和最多2次工具调用。
4. MCP：真实 MCP SDK stdio 进程连接，内置 project_stats、inspect_python。工具只读取上传文件，AST分析不执行代码。连接或调用失败可降级回答。
5. 上下文：top-k 可调；按 UTF-8 字节数保守估算 token 上限；超长片段按查询词抽取源代码行；进程内 LRU 缓存压缩结果。预算只约束引用与工具结果，不包括系统提示、问题、历史、输出，不能等同费用上限。
每次回答包含2次模型调用：规划与生成。会话历史截取最近6条并限制长度。

## 向量模式
默认 local-hash-8192-v1 为离线词汇/字符哈希向量，无模型下载和 embedding 费用。
不是训练得到的语义嵌入，跨语言语义检索能力有限；建议问题包含函数名或错误信息。
可配置独立 OpenAI 兼容嵌入服务：EMBEDDING_MODEL、EMBEDDING_BASE_URL、EMBEDDING_API_KEY。
不能把 DeepSeek 聊天接口当作嵌入接口。更换模型后，在页面删除所有文件后重新上传。
本实现是 SQLite 存储向量 + Python 精确扫描，非大型向量引擎；最多5000个片段，适合个人小项目。

## 上传
支持 Python、JS/TS、Java、C/C++、Go、Rust、C#、HTML、CSS、SQL、Markdown、TXT、JSON、YAML、TOML。
UTF-8 文本，单个文件1 MB，每批5000个文件。同路径覆盖，内容不变跳过。
隐藏目录、依赖目录和常见凭据文件会被过滤；请检查源码里是否另有硬编码密钥。
知识库存放 data/index.sqlite3。项目文件夹保留上传相对路径。
知识库为单个共享空间，无多用户和项目权限隔离。
聊天只在页面内存中保存，刷新清空；新建对话保留知识库。
仅被选入提示的引用、问题、历史和工具结果发送给 DeepSeek。
启用远程 embedding 时文档片段也发送给嵌入服务。

## 接入其他 MCP 服务
修改 mcp_servers.json 的 servers：
{
  "name": "docs",
  "command": "python",
  "args": ["E:/my-tools/docs_server.py"],
  "allowed_tools": ["search_docs"]
}
上述对象放入 servers 数组，接入可信的只读 stdio MCP 服务。
工具名会变为 docs__search_docs。需要自行安装该服务。
网页不可配置任意命令；服务端配置被视为可信。本项目不会自动执行/修改上传代码。
默认只有内置工具；没有虚构外部工具结果。

## 开发
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
API 文档：http://127.0.0.1:8000/api/docs
app/main.py：API、输入验证、本机访问限制
app/store.py：索引、向量、裁剪
app/agent.py：LangGraph 与 MCP 客户端
app/mcp_server.py：独立只读工具服务器
static/：中文响应式网站，无 CDN 依赖
tests/：索引、预算、API、Agent、MCP 回归验证

## 边界
只适合本机个人使用，未实现公网登录鉴权、配额与多租户。启动脚本固定监听127.0.0.1。
前端安全地以文本显示模型输出，仅支持基础标题及代码块，不是完整 Markdown 引擎。
模型建议需要人工确认；运行记录随阶段更新，回答通过 SSE 实时流式显示。
上下文压缩为抽取式压缩，部分信息可能丢失，可提高预算或减少 top-k。
DeepSeek 认证、余额和网络失败会明确报错。

## 官方参考
https://api-docs.deepseek.com/
https://docs.langchain.com/oss/python/langgraph/graph-api
https://github.com/modelcontextprotocol/python-sdk/tree/v1.x

## 验证与复现
详见 验证记录.md。精确版本记录在 requirements-lock.txt；可使用 pip install -r requirements-lock.txt 复现。
本机已预加载 examples 两个文件，首次打开即可提问。源码压缩包不附带数据库，需手动上传示例。

## 流式回答
网页使用 POST /api/chat/stream，模型通过 astream 实时生成文本，以 SSE 事件传给浏览器。分析、检索和工具执行期间显示状态；进入方案生成后开始逐段输出。规划 JSON 不作为回答展示。
完成后渲染代码块、显示引用并写入会话历史。网络或模型中断时保留已收到的文字，并标注回答未完成，不写入完整对话历史。浏览器断开后取消生成并清理 MCP 连接。
原 POST /api/chat JSON 接口继续可用。SSE 事件类型：status、metadata、delta、done、error，另含心跳。
更新后需重启服务并刷新页面。前端解析测试：node tests/test_stream.cjs。

上传数量限制已调整为每批5000个，同时调整 multipart 解析器限制。文件数量与索引片段数量是不同限制；知识库仍最多5000个片段，较长文件会产生多个片段，超出容量的文件会单独报告索引失败。
