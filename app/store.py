"""Persistent small-project vector store with function-aware source chunks."""
import ast
import hashlib
import json
import math
import os
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from langchain_core.embeddings import Embeddings

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "index.sqlite3"
ALLOWED = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h",
           ".go", ".rs", ".cs", ".html", ".css", ".sql", ".md", ".txt", ".json", ".yaml", ".yml", ".toml"}

def safe_name(name):
    normalized = name.replace("\\", "/")
    parts = normalized.split("/")
    if normalized.startswith("/") or any(p in {"", ".", ".."} or ":" in p for p in parts):
        raise ValueError("文件路径不合法")
    if any(p.startswith(".") or p in {"node_modules", "__pycache__", "data"} for p in parts):
        raise ValueError("跳过隐藏文件、依赖目录及数据目录")
    if Path(name).suffix.lower() not in ALLOWED or re.search(r"(secret|credential|private.?key)", name, re.I):
        raise ValueError("不支持该格式或文件可能含凭据")
    return normalized

def token_cost(text):
    # UTF-8 byte count is a conservative upper bound, not provider billing tokens.
    return len(text.encode("utf-8"))

def clip(text, budget):
    return text.encode("utf-8")[:max(0, budget)].decode("utf-8", errors="ignore")

class LocalEmbeddings(Embeddings):
    def embed_query(self, text):
        text = text.lower()
        terms = re.findall(r"[a-z_][a-z_0-9]*|[\u4e00-\u9fff]", text)
        terms += [text[i:i+3] for i in range(max(0, len(text)-2))]
        vector = {}
        for term in terms:
            key = str(int.from_bytes(hashlib.blake2b(term.encode(), digest_size=4).digest(), "big") % 8192)
            vector[key] = vector.get(key, 0) + 1
        norm = math.sqrt(sum(v*v for v in vector.values())) or 1
        return {k: v/norm for k, v in vector.items()}
    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]

@lru_cache(maxsize=1)
def embedder():
    if os.getenv("EMBEDDING_MODEL"):
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(model=os.environ["EMBEDDING_MODEL"],
             api_key=os.environ.get("EMBEDDING_API_KEY") or "missing",
             base_url=os.environ.get("EMBEDDING_BASE_URL"))
    return LocalEmbeddings()

def embedding_id():
    return os.getenv("EMBEDDING_MODEL") or "local-hash-8192-v1"

def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript("""
    CREATE TABLE IF NOT EXISTS files(name TEXT PRIMARY KEY, content TEXT, digest TEXT);
    CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, name TEXT, start INTEGER, end INTEGER, content TEXT, vector TEXT);
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
    """)
    return db

def split_code(name, content):
    lines = content.splitlines()
    boundaries = {0, len(lines)}
    if name.endswith(".py"):
        try:
            tree = ast.parse(content)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    start = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
                    boundaries.update((start, node.end_lineno))
        except SyntaxError:
            pass
    points = sorted(boundaries)
    for a, b in zip(points, points[1:]):
        # Split long functions with overlap; cap individual lines to prevent oversized prompts.
        pos = a
        while pos < b:
            end = min(pos + 70, b)
            text = "\n".join(clip(x, 2000) for x in lines[pos:end])
            if text.strip():
                yield {"name": name, "start": pos+1, "end": end, "content": text}
            if end == b:
                break
            pos = end-10

def put_file(name, content):
    name = safe_name(name)
    if "\x00" in content:
        raise ValueError("不支持二进制文件")
    digest = hashlib.sha256(content.encode()).hexdigest()
    with connect() as db:
        old = db.execute("SELECT digest FROM files WHERE name=?", (name,)).fetchone()
        meta = db.execute("SELECT value FROM meta WHERE key='embedding'", ()).fetchone()
        if meta and meta[0] != embedding_id():
            raise ValueError("向量模型已改变，请清空知识库后重新上传")
        if old and old[0] == digest:
            return 0
        pieces = list(split_code(name, content))
        count = db.execute("SELECT count(*) FROM chunks WHERE name<>?", (name,)).fetchone()[0]
        if count + len(pieces) > 5000:
            raise ValueError("本地索引最多 5000 个片段，请删除不需要的文件")
        vectors = embedder().embed_documents([p["content"] for p in pieces]) if pieces else []
        db.execute("INSERT OR REPLACE INTO files VALUES(?,?,?)", (name, content, digest))
        db.execute("DELETE FROM chunks WHERE name=?", (name,))
        for p, v in zip(pieces, vectors):
            db.execute("INSERT INTO chunks(name,start,end,content,vector) VALUES(?,?,?,?,?)",
                       (name, p["start"], p["end"], p["content"], json.dumps(v)))
        db.execute("INSERT OR REPLACE INTO meta VALUES('embedding',?)", (embedding_id(),))
    return len(pieces)

def inventory():
    with connect() as db:
        return [dict(r) for r in db.execute(
            "SELECT f.name, length(f.content) AS chars, count(c.id) AS chunks FROM files f LEFT JOIN chunks c ON c.name=f.name GROUP BY f.name ORDER BY f.name")]

def delete_file(name):
    with connect() as db:
        db.execute("DELETE FROM chunks WHERE name=?", (name,))
        db.execute("DELETE FROM files WHERE name=?", (name,))
        if not db.execute("SELECT 1 FROM files LIMIT 1").fetchone():
            db.execute("DELETE FROM meta")

def retrieve(query, top_k):
    with connect() as db:
        meta = db.execute("SELECT value FROM meta WHERE key='embedding'").fetchone()
        if meta and meta[0] != embedding_id():
            raise ValueError("向量模型已改变，请清空知识库后重建索引")
        rows = db.execute("SELECT * FROM chunks").fetchall()
    if not rows:
        return []
    q = embedder().embed_query(query)
    results = []
    for row in rows:
        v = json.loads(row["vector"])
        score = sum(x*q.get(k, 0) for k, x in v.items()) if isinstance(v, dict) else sum(a*b for a,b in zip(v,q)) / ((sum(a*a for a in v)*sum(b*b for b in q))**0.5 or 1)
        if score > 0:
            results.append({k: row[k] for k in ("name","start","end","content")} | {"score": round(score, 4)})
    return sorted(results, key=lambda x: x["score"], reverse=True)[:top_k]

@lru_cache(maxsize=256)
def compress_chunk(content, query, budget):
    if token_cost(content) <= budget:
        return content
    # Extractive compression: retain relevant source lines, never invent code.
    lines = content.splitlines()
    terms = set(re.findall(r"\w+", query.lower()))
    ranked = sorted(range(len(lines)), key=lambda i: sum(t in lines[i].lower() for t in terms), reverse=True)
    keep = set()
    used = 0
    for i in ranked:
        cost = token_cost(lines[i])+16
        if used+cost <= budget:
            keep.add(i)
            used += cost
    return clip("\n".join(f"[片段内第{i+1}行] {lines[i]}" for i in sorted(keep)), budget)

def pack_context(hits, query, budget):
    output, sources = [], []
    remaining = budget
    for h in hits:
        label = f"[{h['name']}:{h['start']}-{h['end']}]"
        allowance = min(2400, remaining-token_cost(label)-2)
        if allowance < 100:
            break
        body = compress_chunk(h["content"], query, allowance)
        text = label+"\n"+body
        remaining -= token_cost(text)+2
        output.append(text)
        sources.append({k:v for k,v in h.items() if k != "content"} | {"excerpt":body})
    return "\n\n".join(output), sources
