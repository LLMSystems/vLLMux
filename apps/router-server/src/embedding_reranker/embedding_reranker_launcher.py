import argparse
import asyncio

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from vllm.logger import init_logger

from src.embedding_reranker.embedding_engine.generator import \
    EmbedRerankBuilder
from src.embedding_reranker.schema import EmbeddingRequest
from src.llm_router.config_loader import load_config

logger = init_logger(__name__)

app = FastAPI(title="Embedding Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

builder = None

@app.post("/v1/embeddings")
async def create_embeddings(request: EmbeddingRequest):
    model_name = request.model
    
    if request.query is not None:
        if model_name not in builder.reranking_model_configs:
            raise HTTPException(status_code=400, detail=f"Re-ranking model {model_name} not found")
    else:
        if model_name not in builder.embedding_model_configs:
            raise HTTPException(status_code=400, detail=f"Embedding model {model_name} not found")
        
    if isinstance(request.input, str):
        inputs = [request.input]
    else:
        inputs = request.input

    try:
        model = getattr(builder, model_name)
        response_data = []

        if request.query is not None:
            scores = await asyncio.to_thread(model.rerank, request.query, inputs)
            for idx, score in enumerate(scores):
                response_data.append({
                    "object": "reranking",
                    "embedding": float(score),
                    "index": idx
                })
            return {
                "object": "list",
                "data": response_data,
                "model": model_name,
                "usage": {
                    "prompt_tokens": len(request.query.split()),
                    "total_tokens": sum(len(t.split()) for t in inputs)
                }
            }
        else:
            embeddings = await asyncio.to_thread(model.get_embeddings, inputs)
            for idx, embedding in enumerate(embeddings):
                response_data.append({
                    "object": "embedding",
                    "embedding": embedding.tolist(),
                    "index": idx
                })
            return {
                "object": "list",
                "data": response_data,
                "model": model_name,
                "usage": {
                    "prompt_tokens": sum(len(t.split()) for t in inputs),
                    "total_tokens": sum(len(t.split()) for t in inputs)
                }
            }
    except Exception as e:
        logger.error(f"Error creating embeddings: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error generating embeddings: {str(e)}")

        
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, default="./configs/config.yaml", help="Path to the config file")
    args = parser.parse_args()

    config = load_config(args.config)
    
    global builder
    builder = EmbedRerankBuilder(config_path=args.config, logger=logger)
    
    server_cfg = config.get("embedding_server", {})
    host = server_cfg.get("host", "localhost")
    port = server_cfg.get("port", 8003)
    
    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=False,
        log_level=server_cfg.get("uvicorn_log_level", "info")
    )
        
if __name__ == "__main__":
    main()
    