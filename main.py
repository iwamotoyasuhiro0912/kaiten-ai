"""
🍣 Kaiten AI Gateway
複数プロバイダーのAPIキーを日替わりローテーションするゲートウェイ
"""

from fastapi import FastAPI, HTTPException, Query, Depends, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
import sqlite3
import litellm
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional, List
import os
from contextlib import asynccontextmanager
from pydantic import BaseModel

litellm.set_verbose = False  # ログ抑制

# ── 設定 ─────────────────────────────────────────────────────
DB_PATH    = os.getenv("DB_PATH",    "data/kaiten.db")
ROOT_PATH  = os.getenv("ROOT_PATH",  "/kaiten-ai")   # nginx プレフィックス
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

# ── HTTP Basic認証 ────────────────────────────────────────────
security = HTTPBasic(auto_error=False)

def require_auth(credentials: Optional[HTTPBasicCredentials] = Depends(security)):
    """ADMIN_TOKENが設定されていれば Basic認証を要求する"""
    if not ADMIN_TOKEN:
        return
    # credentials が None = 認証情報が送られていない場合
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="認証が必要です",
            headers={"WWW-Authenticate": "Basic"},
        )
    ok_user = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        ADMIN_USER.encode("utf-8"),
    )
    ok_pass = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        ADMIN_TOKEN.encode("utf-8"),
    )
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="認証失敗",
            headers={"WWW-Authenticate": "Basic"},
        )

# ── 無料プロバイダーのみ ─────────────────────────────────────
PROVIDER_MAP = {
    "groq":       "groq",
    "gemini":     "gemini",
    "google":     "gemini",
    "cerebras":   "cerebras",
    "openrouter": "openrouter",
}

# プロバイダープレフィックスなしのデフォルトモデル名
# （custom_llm_provider使用のためプレフィックス不要）
DEFAULT_MODELS = {
    "groq":       "qwen/qwen3.6-27b",
    "gemini":     "gemini-2.0-flash",
    "cerebras":   "llama3.1-8b",
    "openrouter": "qwen/qwen3.6-27b",
}

def get_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL, api_key TEXT NOT NULL,
                label TEXT DEFAULT '', is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                last_used TEXT, total_usage INTEGER DEFAULT 0,
                today_usage INTEGER DEFAULT 0, today_date TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS request_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT, model TEXT, key_id INTEGER,
                status TEXT, tokens INTEGER DEFAULT 0,
                latency_ms INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
        """)
        conn.commit()

def get_jst_day_index():
    return datetime.now(timezone(timedelta(hours=9))).toordinal()

def get_jst_date_str():
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")

def get_active_key(provider):
    with get_db() as conn:
        keys = conn.execute(
            "SELECT * FROM api_keys WHERE provider=? AND is_active=1 ORDER BY id", (provider,)
        ).fetchall()
    if not keys: return None
    return dict(keys[get_jst_day_index() % len(keys)])

def list_keys(provider=None):
    with get_db() as conn:
        q = "SELECT id, provider, label, is_active, created_at, last_used, total_usage, today_usage, today_date, substr(api_key,1,6)||'...'||substr(api_key,-4) as key_preview FROM api_keys"
        p = ()
        if provider: q += " WHERE provider=?"; p = (provider,)
        q += " ORDER BY provider, id"
        return [dict(r) for r in conn.execute(q, p).fetchall()]

def add_key(provider, api_key, label=""):
    with get_db() as conn:
        exists = conn.execute("SELECT id FROM api_keys WHERE provider=? AND api_key=?", (provider, api_key)).fetchone()
        if exists: return exists["id"]
        cur = conn.execute("INSERT INTO api_keys (provider, api_key, label) VALUES (?,?,?)", (provider.lower(), api_key, label))
        conn.commit()
        return cur.lastrowid

def delete_key(key_id):
    with get_db() as conn:
        conn.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
        conn.commit()

def update_key(key_id, label=None, is_active=None):
    with get_db() as conn:
        if label is not None: conn.execute("UPDATE api_keys SET label=? WHERE id=?", (label, key_id))
        if is_active is not None: conn.execute("UPDATE api_keys SET is_active=? WHERE id=?", (1 if is_active else 0, key_id))
        conn.commit()

def record_usage(key_id, provider, model, status, tokens, latency_ms):
    today = get_jst_date_str()
    with get_db() as conn:
        conn.execute("UPDATE api_keys SET last_used=datetime('now','localtime'), total_usage=total_usage+1, today_usage=CASE WHEN today_date=? THEN today_usage+1 ELSE 1 END, today_date=? WHERE id=?", (today, today, key_id))
        conn.execute("INSERT INTO request_log (provider,model,key_id,status,tokens,latency_ms) VALUES(?,?,?,?,?,?)", (provider, model, key_id, status, tokens, latency_ms))
        conn.commit()

def inject_no_think(messages: list, litellm_model: str) -> list:
    """Qwen3系思考モデル使用時、先頭ユーザーメッセージに /no_think を注入して思考ブロック出力を抑制する"""
    if "qwen3" not in litellm_model.lower():
        return messages
    import copy
    msgs = copy.deepcopy(messages)
    for msg in msgs:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str) and not content.startswith("/no_think"):
                msg["content"] = "/no_think\n" + content
            break
    return msgs

@asynccontextmanager
async def lifespan(app):
    init_db()
    for env_name, env_val in os.environ.items():
        if env_name.endswith("_API_KEY") and env_val:
            provider_raw = env_name[:-8].lower()
            if provider_raw in PROVIDER_MAP:
                add_key(provider_raw, env_val, f"[env] {env_name}")
    yield

app = FastAPI(title="Kaiten AI Gateway 🍣", version="1.0.0", lifespan=lifespan, root_path=ROOT_PATH)
app.mount("/static", StaticFiles(directory="static"), name="static")

class ChatRequest(BaseModel):
    model: str
    messages: list
    temperature: float = 0.7
    max_tokens: int = 1024
    stream: bool = False

class KeyCreate(BaseModel):
    provider: str
    api_key: str
    label: str = ""

class KeyUpdate(BaseModel):
    label: Optional[str] = None
    is_active: Optional[bool] = None

@app.get("/")
async def root():
    return {"service": "Kaiten AI Gateway 🍣", "status": "ok", "providers": list(PROVIDER_MAP.keys())}

@app.get("/health")
async def health():
    providers = {}
    with get_db() as conn:
        for row in conn.execute("SELECT provider, COUNT(*) as total, SUM(is_active) as active FROM api_keys GROUP BY provider").fetchall():
            prov = row["provider"]
            providers[prov] = {"total": row["total"], "active": int(row["active"] or 0)}
    return {"status": "ok", "providers": providers}

@app.post("/v1/chat")
async def chat(req: ChatRequest):
    if "/" in req.model:
        provider_raw, model_suffix = req.model.split("/", 1)
    else:
        provider_raw = req.model
        model_suffix = None
    provider = provider_raw.lower()
    if provider not in PROVIDER_MAP:
        raise HTTPException(400, {"error": f"Unknown provider: '{provider}'", "available": sorted(PROVIDER_MAP.keys())})
    key_info = get_active_key(provider)
    if not key_info:
        raise HTTPException(503, {"error": f"No active API key for '{provider}'", "hint": f"Add keys at /admin or POST /api/keys"})

    litellm_provider = PROVIDER_MAP[provider]

    # bare_model: プロバイダープレフィックスなしのモデル名
    # 例: "groq/qwen/qwen3.6-27b" → provider="groq", bare_model="qwen/qwen3.6-27b"
    bare_model = model_suffix if model_suffix else DEFAULT_MODELS.get(provider, "default")

    # ログ・デバッグ用の表示名（プレフィックス付き）
    litellm_model_display = f"{litellm_provider}/{bare_model}"

    messages_to_send = inject_no_think(req.messages, litellm_model_display)

    import time
    t0 = time.time()
    try:
        # ★ FIX: custom_llm_provider を明示することで litellm の内部モデルバリデーションを
        # バイパスし、bare_model をそのまま API に渡す。
        # これにより groq/qwen/qwen3.6-27b 等の新しいモデルが llama-3.3-70b-versatile に
        # サイレントフォールバックされるバグを修正。
        response = await litellm.acompletion(
            model=bare_model,
            custom_llm_provider=litellm_provider,
            messages=messages_to_send,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            api_key=key_info["api_key"]
        )
        latency = int((time.time() - t0) * 1000)
        tokens = getattr(response.usage, "total_tokens", 0) if hasattr(response, "usage") else 0
        record_usage(key_info["id"], provider, litellm_model_display, "ok", tokens, latency)
        return response
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        record_usage(key_info["id"], provider, litellm_model_display, "error", 0, latency)
        raise HTTPException(500, {"error": str(e), "provider": provider, "model": litellm_model_display})

@app.get("/api/keys")
async def api_list_keys(provider: Optional[str] = Query(None), _: None = Depends(require_auth)):
    return list_keys(provider)

@app.post("/api/keys", status_code=201)
async def api_add_key(req: KeyCreate, _: None = Depends(require_auth)):
    prov = req.provider.lower()
    if prov not in PROVIDER_MAP: raise HTTPException(400, {"error": f"Unknown provider: {prov}", "available": sorted(PROVIDER_MAP.keys())})
    if not req.api_key.strip(): raise HTTPException(400, {"error": "api_key cannot be empty"})
    key_id = add_key(prov, req.api_key.strip(), req.label)
    return {"status": "created", "id": key_id, "provider": prov}

@app.patch("/api/keys/{key_id}")
async def api_update_key(key_id: int, req: KeyUpdate, _: None = Depends(require_auth)):
    update_key(key_id, label=req.label, is_active=req.is_active)
    return {"status": "updated", "id": key_id}

@app.delete("/api/keys/{key_id}")
async def api_delete_key(key_id: int, _: None = Depends(require_auth)):    
    delete_key(key_id)
    return {"status": "deleted", "id": key_id}

@app.get("/api/rotation")
async def api_rotation(_: None = Depends(require_auth)):
    day_idx = get_jst_day_index()
    jst = datetime.now(timezone(timedelta(hours=9)))
    next_midnight = jst.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    seconds_until = int((next_midnight - jst).total_seconds())
    result = {"today_jst": jst.strftime("%Y-%m-%d"), "next_rotation_jst": next_midnight.strftime("%Y-%m-%d 00:00 JST"), "seconds_until_rotation": seconds_until, "providers": {}}
    with get_db() as conn:
        provs = [r[0] for r in conn.execute("SELECT DISTINCT provider FROM api_keys WHERE is_active=1").fetchall()]
        for prov in provs:
            keys = conn.execute("SELECT id, label, substr(api_key,1,6)||'...'||substr(api_key,-4) as preview, today_usage FROM api_keys WHERE provider=? AND is_active=1 ORDER BY id", (prov,)).fetchall()
            if not keys: continue
            active_idx = day_idx % len(keys)
            result["providers"][prov] = {"total_keys": len(keys), "active_index": active_idx, "active_key": dict(keys[active_idx]), "all_keys": [dict(k) for k in keys]}
    return result

@app.get("/api/log")
async def api_log(limit: int = Query(50, le=500), _: None = Depends(require_auth)):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM request_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]

@app.get("/admin", response_class=HTMLResponse)
async def admin_ui(_: None = Depends(require_auth)):
    path = "static/index.html"
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f: return f.read()
    return "<h1>🍣 Kaiten AI</h1><p>static/index.html not found</p>"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8300)), reload=False, workers=1)
