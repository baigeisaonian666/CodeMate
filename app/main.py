import asyncio
import json
from contextlib import suppress
import os
from pathlib import Path
from typing import Literal
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT/".env")

from fastapi import FastAPI, HTTPException, Request
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from app.store import inventory, put_file, delete_file, embedding_id
from app.agent import run_agent

app = FastAPI(title="CodeMate", docs_url="/api/docs")
app.mount("/static",StaticFiles(directory=ROOT/"static"),name="static")
gate = asyncio.Semaphore(2)

@app.middleware("http")
async def local_only(request: Request, call_next):
    # Bind loopback; defend against DNS rebinding and cross-origin writes.
    host = request.headers.get("host","").split(":")[0]
    if host not in {"127.0.0.1","localhost","testserver"}:
        return JSONResponse({"detail":"仅支持本机访问"},status_code=403)
    if request.method not in {"GET","HEAD","OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin not in {"http://"+request.headers.get("host",""),"https://"+request.headers.get("host","")}:
            return JSONResponse({"detail":"拒绝跨站请求"},status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
    return response

@app.get("/")
def home():
    return FileResponse(ROOT/"static"/"index.html")

@app.get("/api/status")
def status():
    files = inventory()
    return {"configured":bool(os.getenv("DEEPSEEK_API_KEY")), "model":os.getenv("DEEPSEEK_MODEL","deepseek-flash"),
            "embedding":embedding_id(),"files":files,"chunks":sum(f["chunks"] for f in files)}

MAX_UPLOAD_FILES = 5000

@app.post("/api/files")
async def upload(request: Request):
    # Explicit parsing also raises Starlette's default 1000-file ceiling.
    try:
        form = await request.form(max_files=MAX_UPLOAD_FILES)
    except StarletteHTTPException as exc:
        if "Too many files" in str(exc.detail):
            raise HTTPException(400, f"每次最多上传{MAX_UPLOAD_FILES}个文件") from None
        raise
    try:
        files = form.getlist("files")
        if not files or any(not isinstance(file, UploadFile) for file in files):
            raise HTTPException(422, "请通过 files 字段上传文件")
        if len(files) > MAX_UPLOAD_FILES:
            raise HTTPException(400, f"每次最多上传{MAX_UPLOAD_FILES}个文件")
        results = []
        for file in files:
            try:
                raw = await file.read(1_000_001)
                if len(raw)>1_000_000:
                    raise ValueError("单文件不能超过1MB")
                content = raw.decode("utf-8-sig")
                count = await asyncio.to_thread(put_file,file.filename or "",content)
                results.append({"name":file.filename,"chunks":count,"ok":True})
            except (ValueError,UnicodeError) as e:
                results.append({"name":file.filename,"ok":False,"error":str(e)})
            except Exception:
                results.append({"name":file.filename,"ok":False,"error":"索引失败，请检查向量模型配置"})
            finally:
                await file.close()
        return {"results":results}
    finally:
        await form.close()

@app.delete("/api/files")
def remove(name: str):
    delete_file(name)
    return {"ok":True}

class Message(BaseModel):
    role: Literal["user","assistant"]
    content: str = Field(max_length=20000)

class Chat(BaseModel):
    question: str = Field(min_length=1,max_length=4000)
    history: list[Message] = Field(default_factory=list,max_length=12)
    top_k: int = Field(default=5,ge=1,le=12)
    budget: int = Field(default=8000,ge=1000,le=24000)
    use_tools: bool = True

@app.post("/api/chat")
async def chat(body: Chat):
    if not body.question.strip():
        raise HTTPException(400,"请输入问题")
    try:
        async with gate:
            result = await asyncio.wait_for(run_agent(body.model_dump()),240)
        return {k:result.get(k) for k in ("answer","trace","sources","context_cost")}
    except ValueError as e:
        raise HTTPException(400,str(e))
    except TimeoutError:
        raise HTTPException(504,"请求超时，请缩短问题或稍后重试")
    except Exception as e:
        code = getattr(e,"status_code",None)
        detail = {401:"DeepSeek API key 无效，请更新 .env 后重启",
                  402:"DeepSeek 余额不足",429:"DeepSeek 请求过于频繁，请稍后重试"}.get(code,
                  "模型请求失败，请检查 API key、模型名称、网络或服务余额")
        raise HTTPException(502,detail) from None

def stream_error(exc):
    if isinstance(exc, TimeoutError):
        return "请求超时，已保留收到的部分内容，请重试"
    if isinstance(exc, ValueError):
        return str(exc)
    return {401: "DeepSeek API key 无效，请更新 .env 后重启",
            402: "DeepSeek 余额不足",
            429: "请求过于频繁，请稍后重试"}.get(
        getattr(exc, "status_code", None),
        "模型连接中断，请检查网络、模型配置或余额后重试")


async def chat_events(state):
    # The producer owns the MCP context throughout its lifetime.
    # Bounded queue provides backpressure; disconnect cancels model and MCP work.
    queue = asyncio.Queue(maxsize=64)

    async def produce():
        try:
            async with asyncio.timeout(240):
                async with gate:
                    result = await run_agent(state, emit=queue.put)
                    await queue.put({"type": "done", **{
                        k: result.get(k) for k in ("answer", "trace", "sources", "context_cost")}})
        except Exception as exc:
            await queue.put({"type": "error", "detail": stream_error(exc)})

    task = asyncio.create_task(produce())
    try:
        yield 'data: {"type":"status","detail":"正在准备工作流…"}\n\n'
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=10)
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            if event["type"] in {"done", "error"}:
                break
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@app.post("/api/chat/stream")
async def stream_chat(body: Chat):
    if not body.question.strip():
        raise HTTPException(400, "请输入问题")
    return StreamingResponse(chat_events(body.model_dump()), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
