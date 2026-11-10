"""Read-only built-in MCP server; source is limited to explicitly uploaded files."""
import ast
from mcp.server.fastmcp import FastMCP
from app.store import connect, inventory, clip

mcp = FastMCP("CodeMate project tools")

@mcp.tool()
def project_stats() -> str:
    """List uploaded project files and their indexed chunk counts."""
    import json
    return clip(json.dumps(inventory(), ensure_ascii=False), 6000)

@mcp.tool()
def inspect_python(path: str) -> str:
    """Inspect syntax, functions and classes in an uploaded Python file. Never execute code."""
    import json
    with connect() as db:
        row = db.execute("SELECT content FROM files WHERE name=?", (path,)).fetchone()
    if not row:
        return "文件未上传"
    if not path.endswith(".py"):
        return "仅支持 Python AST 分析"
    try:
        tree = ast.parse(row[0])
    except SyntaxError as exc:
        return f"语法错误，第 {exc.lineno} 行：{exc.msg}"
    result = [{"kind":type(n).__name__, "name":n.name,"line":n.lineno}
              for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
    return clip(json.dumps({"syntax":"valid","symbols":result},ensure_ascii=False),6000)

if __name__ == "__main__":
    mcp.run(transport="stdio")
