# @role: ローカルで稼働するマクロ生成用のLLM（Gemma等）をホスティングし、FastAPIを通じて推論APIを提供する。
#
# 【起動元】
#   - engines/manager.py (サブプロセスとして uvicorn 経由で起動される)

import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel
from llama_cpp import Llama
import logging

logger = logging.getLogger(__name__)

# --- グローバル変数 ---
llm_instance = None
llm_lock = threading.Lock() # 同時リクエスト時の競合防止

@asynccontextmanager
async def lifespan(app: FastAPI):
    """サーバーの起動時と終了時のライフサイクルを管理する"""
    global llm_instance
    try:
        # プロジェクトルートに配置されたGGUFモデルを読み込む
        # ※ モデルファイル名は必要に応じて変更してください
        model_path = "./gemma-4-E2B-it-Q4_K_M.gguf"
        
        logger.info(f"Loading LLM model from {model_path} ...")
        llm_instance = Llama(
            model_path=model_path,
            n_ctx=2048,
        )
        logger.info("LLM model loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load LLM model: {e}")
        llm_instance = None
        
    yield  # ここでサーバーがリクエストの待ち受けを開始します
    
    # サーバー終了時のメモリ解放処理
    if llm_instance:
        logger.info("Unloading LLM model and freeing memory...")
        del llm_instance

# --- FastAPI アプリケーションの定義 ---
app = FastAPI(lifespan=lifespan)

class GenerateRequest(BaseModel):
    prompt: str
    image: str | None = None  # Base64エンコードされた画像データ

@app.post("/generate")
async def generate_text(req: GenerateRequest):
    global llm_instance
    if not llm_instance:
        return {"success": False, "error": "Model is not loaded or failed to initialize."}
    
    try:
        messages = []
        content = [{"type": "text", "text": req.prompt}]
        
        # 画像が指定されている場合、マルチモーダル対応のフォーマットで追加
        if req.image:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{req.image}"
                }
            })
        
        messages.append({"role": "user", "content": content})
        
        # 推論の実行（スレッドセーフに実行）
        with llm_lock:
            response = llm_instance.create_chat_completion(
                messages=messages,
                max_tokens=1024
            )
        return {"success": True, "response": response}
    except Exception as e:
        logger.error(f"Error during LLM generation: {e}")
        return {"success": False, "error": str(e)}