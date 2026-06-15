# @role: 独立したバックグラウンドプロセスとして稼働し、LLM(GGUF)への推論リクエストを処理するFastAPIサーバー。
#
# 【背景】
#   - UIスレッドのフリーズを防ぐため、アプリ本体とは別プロセスとして起動される。
#   - llama_cpp-python は同時リクエスト時の状態管理がシビアなため、threading.Lockで排他制御を行う。

import threading
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
from llama_cpp import Llama

app = FastAPI()

# GGUFモデルのパス (要件に合わせて環境変数や設定ファイルからの読み込みに拡張可能)
MODEL_PATH = "./gemma-4-E2B-it-Q4_K_M.gguf"

# AIモデルのシングルトンインスタンスと排他ロック
llm_instance: Optional[Llama] = None
llm_lock = threading.Lock()

class GenerateRequest(BaseModel):
    prompt: str
    image: Optional[str] = None  # Base64エンコードされた画像データ

@app.on_event("startup")
def load_model():
    """サーバー起動時にAIモデルをVRAM/RAMにロードする"""
    global llm_instance
    try:
        llm_instance = Llama(
            model_path=MODEL_PATH,
            n_ctx=2048,
        )
    except Exception as e:
        # モデルのロード失敗は致命的なため、起動時にわかるよう例外を投げる
        raise RuntimeError(f"Failed to load LLM model from {MODEL_PATH}: {e}")

@app.on_event("shutdown")
def unload_model():
    """サーバー終了時にメモリを安全に解放する"""
    global llm_instance
    if llm_instance:
        del llm_instance
        llm_instance = None

@app.post("/generate")
async def generate_text(req: GenerateRequest):
    """テキストおよび画像(マルチモーダル)を受け取り、推論結果を返す"""
    global llm_instance
    if not llm_instance:
        raise HTTPException(status_code=503, detail="Model is not loaded")
    
    try:
        messages = []
        content = [{"type": "text", "text": req.prompt}]
        
        if req.image:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{req.image}"
                }
            })
        
        messages.append({"role": "user", "content": content})
        
        # 複数リクエストによるコンテキストの破壊を防ぐためロックを取得
        with llm_lock:
            response = llm_instance.create_chat_completion(
                messages=messages,
                max_tokens=1024
            )
        return {"success": True, "response": response}
    
    except Exception as e:
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    # このファイルが直接実行された場合のフォールバック設定 (通常はmanagerからuvicorn経由で起動)
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8844)