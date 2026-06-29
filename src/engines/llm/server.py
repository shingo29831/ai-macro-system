# @role: ローカルで稼働するマクロ生成用のLLM（Gemma等）をホスティングし、FastAPIを通じて推論APIを提供する。
#
# 【起動元】
#   - engines/manager.py (サブプロセスとして uvicorn 経由で起動される)

import threading
import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel
from llama_cpp import Llama
import logging

logger = logging.getLogger(__name__)

llm_instance = None
llm_lock = threading.Lock()

@asynccontextmanager
async def lifespan(app: FastAPI):
    global llm_instance
    try:
        # To avoid current directory dependency, resolve the project root dynamically.
        # Allow overriding via environment variable for flexibility.
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        default_model_path = project_root / "gemma-4-E2B-it-Q4_K_M.gguf"
        model_path = os.getenv("LLM_MODEL_PATH", str(default_model_path))
        
        logger.info(f"Loading LLM model from {model_path} ...")
        
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model file not found at: {model_path}")
            
        llm_instance = Llama(
            model_path=model_path,
            n_ctx=8192,
        )
        logger.info("LLM model loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load LLM model: {e}")
        llm_instance = None
        
    yield
    
    if llm_instance:
        logger.info("Unloading LLM model and freeing memory...")
        del llm_instance

app = FastAPI(lifespan=lifespan)

class GenerateRequest(BaseModel):
    prompt: str
    image: str | None = None

@app.post("/generate")
async def generate_text(req: GenerateRequest):
    global llm_instance
    if not llm_instance:
        return {"success": False, "error": "Model is not loaded or failed to initialize."}
    
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
        
        with llm_lock:
            response = llm_instance.create_chat_completion(
                messages=messages,
                max_tokens=1024
            )
        return {"success": True, "response": response}
    except Exception as e:
        logger.error(f"Error during LLM generation: {e}")
        return {"success": False, "error": str(e)}