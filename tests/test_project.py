import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from app import store
from app.main import app
from app.agent import build_graph, ToolHub

@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,"DB",tmp_path/"index.sqlite3")
    monkeypatch.delenv("EMBEDDING_MODEL",raising=False)
    store.embedder.cache_clear()
    yield
    store.embedder.cache_clear()

def test_index_replace_delete():
    assert store.put_file("math.py","def divide(a,b):\n    return a/b\n") == 1
    assert store.put_file("math.py","def divide(a,b):\n    return a/b\n") == 0
    assert store.retrieve("divide",3)[0]["name"] == "math.py"
    store.put_file("math.py","def add(a,b):\n    return a+b\n")
    assert len(store.inventory())==1
    assert "add" in store.retrieve("add",3)[0]["content"]
    store.delete_file("math.py")
    assert store.inventory()==[]

@pytest.mark.parametrize("name",["../oops.py","/oops.py","C:/secret.py",".env","a/.git/config.py","private_key.txt","node_modules/a.js"])
def test_paths(name):
    with pytest.raises(ValueError):
        store.safe_name(name)

def test_chunk_lines_and_budget():
    code="\n".join(["# heading","@decorator","def example():"]+["    x = '中文 value'"]*200)
    pieces=list(store.split_code("x.py",code))
    assert any(p["start"]==2 for p in pieces)
    for p in pieces:
        assert p["content"]=="\n".join(code.splitlines()[p["start"]-1:p["end"]])
    packed,sources=store.pack_context(pieces,"中文",1200)
    assert store.token_cost(packed)<=1200
    assert sources
    assert store.token_cost(store.clip("中文"*100,101))<=101

def test_api():
    c=TestClient(app)
    assert c.get("/").status_code==200
    r=c.post("/api/files",files=[("files",("sample.py",b"def sample():\n return 1\n","text/plain"))])
    assert r.json()["results"][0]["ok"]
    assert c.get("/api/status").json()["chunks"]==1
    assert c.post("/api/chat",json={"question":"x","top_k":100}).status_code==422
    assert c.post("/api/chat",json={"question":"x"},headers={"origin":"https://evil.example"}).status_code==403
    assert c.get("/",headers={"host":"evil.example"}).status_code==403
    assert c.delete("/api/files",params={"name":"sample.py"}).json()["ok"]

class FakeModel:
    def __init__(self):
        self.calls=[]
    async def ainvoke(self,messages):
        self.calls.append(messages)
        if len(self.calls)==1:
            return AIMessage(content=json.dumps({"steps":["查找函数","检查语法"],"query":"average","tools":[{"name":"project__project_stats","arguments":{}}]}))
        return AIMessage(content="使用证据 [calc.py:1-2]，建议检查空列表。")

class FakeHub:
    errors=[]
    called=False
    def catalog(self): return []
    async def call(self,name,args):
        self.called=True
        return "1 file"

def test_agent_graph():
    store.put_file("calc.py","def average(values):\n return sum(values)/len(values)")
    llm=FakeModel();hub=FakeHub()
    result=asyncio.run(build_graph(hub,llm).ainvoke({"question":"average 空列表报错","history":[],"top_k":3,"budget":2000,"use_tools":True}))
    assert hub.called
    assert len(result["trace"])==4
    assert result["sources"][0]["name"]=="calc.py"
    assert result["context_cost"]<=2000
    assert "1 file" in llm.calls[-1][-1].content

def test_no_tools_branch():
    llm=FakeModel()
    r=asyncio.run(build_graph(None,llm).ainvoke({"question":"hello","history":[],"top_k":2,"budget":2000,"use_tools":False}))
    assert len(r["trace"])==3

def test_real_mcp_transport():
    async def run():
        async with ToolHub() as hub:
            assert not hub.errors
            assert "project__inspect_python" in hub.tools
            result=await hub.call("project__inspect_python",{"path":"does-not-exist.py"})
            assert "文件未上传" in result
    asyncio.run(run())

from app import main
from app.agent import run_agent
from langchain_core.messages import AIMessageChunk

def test_stream_graph_emits_before_completion():
    async def run():
        first = asyncio.Event()
        release = asyncio.Event()
        events = []
        class StreamingModel(FakeModel):
            async def astream(self, messages):
                yield AIMessageChunk(content="中文第一段")
                await release.wait()
                yield AIMessageChunk(content="第二段")
        async def emit(event):
            events.append(event)
            if event["type"] == "delta":
                first.set()
        task = asyncio.create_task(build_graph(None, StreamingModel(), emit).ainvoke(
            {"question":"hello","history":[],"top_k":2,"budget":2000,"use_tools":False}))
        await asyncio.wait_for(first.wait(), 5)
        assert not task.done()
        release.set()
        result = await task
        assert result["answer"] == "中文第一段第二段"
        assert "".join(e["text"] for e in events if e["type"]=="delta") == result["answer"]
        assert any(e["type"]=="metadata" for e in events)
    asyncio.run(run())

def test_sse_success_and_error(monkeypatch):
    async def fake(state, emit=None):
        await emit({"type":"delta","text":"中文\n代码"})
        return {"answer":"中文\n代码","sources":[],"trace":[],"context_cost":0}
    monkeypatch.setattr(main,"run_agent",fake)
    c=TestClient(app)
    r=c.post("/api/chat/stream",json={"question":"hi"})
    assert r.headers["content-type"].startswith("text/event-stream")
    events=[json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    assert [e["type"] for e in events]==["status","delta","done"]
    assert events[1]["text"]=="中文\n代码"
    async def broken(state,emit=None):
        await emit({"type":"delta","text":"部分内容"})
        raise RuntimeError("sensitive provider details")
    monkeypatch.setattr(main,"run_agent",broken)
    r=c.post("/api/chat/stream",json={"question":"hi"})
    assert '"type": "error"' in r.text
    assert "sensitive provider" not in r.text
    assert '"type": "done"' not in r.text
    assert c.post("/api/chat/stream",json={"question":" "}).status_code==400

def test_stream_disconnect_cancels_producer(monkeypatch):
    async def run():
        stopped=asyncio.Event()
        async def slow(state,emit=None):
            try:
                await emit({"type":"delta","text":"开始"})
                await asyncio.Event().wait()
            finally:
                stopped.set()
        monkeypatch.setattr(main,"run_agent",slow)
        stream=main.chat_events({})
        await anext(stream)
        assert "delta" in await anext(stream)
        await stream.aclose()
        assert stopped.is_set()
    asyncio.run(run())


def test_upload_5000_files(monkeypatch):
    indexed=[]
    def record(name,content):
        indexed.append(name)
        return 1
    monkeypatch.setattr(main,"put_file",record)
    c=TestClient(app)
    files=[("files",(f"sample_{i}.py",b"x=1","text/plain")) for i in range(5000)]
    response=c.post("/api/files",files=files)
    assert response.status_code==200
    assert len(response.json()["results"])==5000
    assert all(item["ok"] for item in response.json()["results"])
    assert len(indexed)==5000

def test_upload_5001_files_rejected_before_indexing(monkeypatch):
    def unexpected(*args):
        pytest.fail("oversized batch must not be indexed")
    monkeypatch.setattr(main,"put_file",unexpected)
    files=[("files",(f"sample_{i}.py",b"x=1","text/plain")) for i in range(5001)]
    response=TestClient(app).post("/api/files",files=files)
    assert response.status_code==400
    assert response.json()["detail"]=="每次最多上传5000个文件"

def test_upload_missing_file_field():
    c=TestClient(app)
    assert c.post("/api/files",data={"files":"not a file"}).status_code==422
    assert c.post("/api/files").status_code==422
