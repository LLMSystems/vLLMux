from typing import List, Optional, Union

from pydantic import BaseModel


class EmbeddingRequest(BaseModel):
    input: Union[str, List[str]]  # input text or list of input texts
    model: Optional[str] = None  # embedding model name
    encoding_format: Optional[str] = "float"  # encoding format, default is float


class RerankRequest(BaseModel):
    """Jina/Cohere-compatible rerank request (also what vLLM's /v1/rerank takes)."""
    query: str  # the search query
    documents: List[str]  # candidate documents to score against the query
    model: Optional[str] = None  # reranking model name
    top_n: Optional[int] = None  # keep only the top-N results (after sorting)
    return_documents: bool = True  # echo each document's text in the results


class ScoreRequest(BaseModel):
    """vLLM-compatible /v1/score request: pairwise relevance between text_1 and text_2.

    A scalar on either side is broadcast against the list on the other; two lists
    must be equal length and are scored pairwise.
    """
    text_1: Union[str, List[str]]
    text_2: Union[str, List[str]]
    model: Optional[str] = None  # reranking (cross-encoder) model name
