import argparse
import asyncio
import math
import uuid

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from vllm.logger import init_logger

from src.embedding_reranker.embedding_engine.generator import \
    EmbedRerankBuilder
from src.embedding_reranker.schema import (EmbeddingRequest, RerankRequest,
                                           ScoreRequest)
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


def _count_tokens(model, texts) -> int:
    """Real token count for `texts` using the model's own tokenizer.

    `texts` is a list of strings (embeddings) or [a, b] pairs (rerank/score);
    the tokenizer accepts both. Bounded by the model's max_length so the count
    matches what's actually fed to the forward pass. Best-effort: any tokenizer
    quirk degrades to 0 rather than failing the request.
    """
    try:
        enc = model.tokenizer(
            texts, padding=False, truncation=True, max_length=model.max_length
        )
        return sum(len(ids) for ids in enc["input_ids"])
    except Exception:
        return 0


def _sigmoid(x: float) -> float:
    """Map a cross-encoder logit to a [0, 1] relevance score (Jina/Cohere-style)."""
    return 1.0 / (1.0 + math.exp(-x))


def _resolve_reranker(model_name: str):
    """Fetch a loaded reranking model by name or raise a 400/404."""
    if model_name not in builder.reranking_model_configs:
        raise HTTPException(
            status_code=404, detail=f"Re-ranking model '{model_name}' not found"
        )
    return getattr(builder, model_name)


async def _rerank_scores(model, query: str, documents: list[str]) -> list[float]:
    """Run the cross-encoder off the event loop and map logits to [0, 1]."""
    raw = await asyncio.to_thread(model.rerank, query, documents)
    return [_sigmoid(float(s)) for s in raw]


@app.get("/health")
async def health():
    """Readiness probe: 200 only once the model builder has finished loading.

    The dashboard backend's reconciler polls this to mark the embedding server
    READY, the same way it polls vLLM's /health for LLM instances.
    """
    if builder is None:
        raise HTTPException(status_code=503, detail="models still loading")
    return {"status": "ok"}


@app.post("/v1/embeddings")
async def create_embeddings(request: EmbeddingRequest):
    """OpenAI-compatible embeddings. (Reranking moved to /v1/rerank.)"""
    model_name = request.model
    if model_name not in builder.embedding_model_configs:
        raise HTTPException(
            status_code=404, detail=f"Embedding model '{model_name}' not found"
        )

    inputs = [request.input] if isinstance(request.input, str) else request.input

    try:
        model = getattr(builder, model_name)
        embeddings = await asyncio.to_thread(model.get_embeddings, inputs)
        data = [
            {"object": "embedding", "embedding": emb.tolist(), "index": idx}
            for idx, emb in enumerate(embeddings)
        ]
        tokens = _count_tokens(model, inputs)
        return {
            "object": "list",
            "data": data,
            "model": model_name,
            "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating embeddings: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error generating embeddings: {str(e)}")


@app.post("/v1/rerank")
async def rerank(request: RerankRequest):
    """Jina/Cohere-compatible reranking: score `documents` against `query`,
    return them sorted by descending relevance (`relevance_score` in [0, 1])."""
    model_name = request.model
    model = _resolve_reranker(model_name)

    if not request.documents:
        raise HTTPException(status_code=400, detail="'documents' must be non-empty")

    try:
        scores = await _rerank_scores(model, request.query, request.documents)
        results = []
        for idx, score in enumerate(scores):
            item = {"index": idx, "relevance_score": score}
            if request.return_documents:
                item["document"] = {"text": request.documents[idx]}
            results.append(item)
        results.sort(key=lambda r: r["relevance_score"], reverse=True)
        if request.top_n is not None:
            results = results[: request.top_n]

        pairs = [[request.query, doc] for doc in request.documents]
        tokens = _count_tokens(model, pairs)
        return {
            "id": f"rerank-{uuid.uuid4().hex}",
            "model": model_name,
            "results": results,
            "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reranking: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error reranking: {str(e)}")


@app.post("/v1/score")
async def score(request: ScoreRequest):
    """vLLM-compatible pairwise scoring with a cross-encoder reranker.

    A scalar on either side is broadcast against the list on the other; two
    lists must be equal length and are scored pairwise. `score` is in [0, 1]."""
    model_name = request.model
    model = _resolve_reranker(model_name)

    t1 = request.text_1
    t2 = request.text_2
    # Normalise to a list of (a, b) pairs.
    if isinstance(t1, str) and isinstance(t2, str):
        pairs = [(t1, t2)]
    elif isinstance(t1, str):
        pairs = [(t1, b) for b in t2]
    elif isinstance(t2, str):
        pairs = [(a, t2) for a in t1]
    else:
        if len(t1) != len(t2):
            raise HTTPException(
                status_code=400,
                detail="text_1 and text_2 must be equal length when both are lists",
            )
        pairs = list(zip(t1, t2))

    if not pairs:
        raise HTTPException(status_code=400, detail="no text pairs to score")

    try:
        # The cross-encoder scores [query, doc] pairs. Group by the left side so
        # pairs sharing a query batch through a single rerank() call.
        scores: list[float] = [0.0] * len(pairs)
        by_left: dict[str, list[int]] = {}
        for i, (a, _) in enumerate(pairs):
            by_left.setdefault(a, []).append(i)
        for left, idxs in by_left.items():
            docs = [pairs[i][1] for i in idxs]
            left_scores = await _rerank_scores(model, left, docs)
            for i, s in zip(idxs, left_scores):
                scores[i] = s

        data = [
            {"index": i, "object": "score", "score": s}
            for i, s in enumerate(scores)
        ]
        tokens = _count_tokens(model, [list(p) for p in pairs])
        return {
            "id": f"score-{uuid.uuid4().hex}",
            "model": model_name,
            "data": data,
            "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error scoring: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error scoring: {str(e)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, default="../../packages/config-schema/config.yaml", help="Path to the config file")
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
