"""Custom evalscope rerank ApiPlugin for this project's /v1/rerank endpoint.

Our embedding server exposes a Jina/Cohere-style rerank API:

    request : {"query": "...", "documents": [doc, ...], "model": ...}
    response: {"id": "rerank-...", "model": ...,
               "results": [{"index": i, "relevance_score": <float>, "document": {...}}, ...],
               "usage": {"prompt_tokens": N, "total_tokens": N}}

Why not evalscope's built-in ``openai_rerank``? Its request/response handling now
matches us, but evalscope 1.8.1 hard-codes a ``reranks`` (plural) endpoint suffix
in ``_OPENAI_API_ENDPOINT_MAP`` and rewrites any URL that doesn't end in it — so
``/v1/rerank`` becomes ``/v1/rerank/reranks`` (404). Our standard route is the
singular ``/v1/rerank`` (vLLM/Jina/Cohere), so this plugin stays: it POSTs to the
exact URL with no suffix mangling. Importing it registers api='llmops_rerank'.
"""
import json
import sys
import time
import traceback
from typing import Dict, List, Tuple, Union

from evalscope.perf.arguments import Arguments
from evalscope.perf.plugin.api.base import ApiPluginBase
from evalscope.perf.plugin.datasets.utils import load_tokenizer
from evalscope.perf.plugin.registry import register_api
from evalscope.perf.utils.benchmark_util import BenchmarkData
from evalscope.utils.logger import get_logger

logger = get_logger()


@register_api(['llmops_rerank'])
class LlmopsRerankPlugin(ApiPluginBase):
    """Rerank plugin for the project's /v1/rerank endpoint."""

    def __init__(self, param: Arguments):
        super().__init__(param=param)
        self.tokenizer = load_tokenizer(param.tokenizer_path) if param.tokenizer_path else None

    def build_request(self, messages: Union[List[Dict], str, Dict], param: Arguments = None) -> Dict:
        """Map the rerank dataset's {query, documents} into our /v1/rerank body."""
        param = param or self.param
        try:
            query = ''
            documents: List[str] = []
            if isinstance(messages, dict):
                query = messages.get('query', '')
                documents = messages.get('documents', [])
                if isinstance(documents, str):
                    documents = [documents]
            elif isinstance(messages, list) and messages:
                # First item is the query, the rest are documents.
                query = messages[0] if isinstance(messages[0], str) else messages[0].get('content', '')
                documents = [m if isinstance(m, str) else m.get('content', '') for m in messages[1:]]
            elif isinstance(messages, str):
                query = messages
                documents = ['This is a test document for reranking.']

            if not documents:
                return None

            payload = {'query': query, 'documents': documents, 'model': param.model}
            if param.extra_args:
                # Don't leak dataset-generation knobs (num_documents…) into the body.
                payload.update({k: v for k, v in param.extra_args.items()
                                if k not in ('num_documents', 'document_length_ratio', 'batch_size')})
            return payload
        except Exception as e:
            logger.exception(f'Failed to build rerank request: {e}')
            return None

    def parse_responses(self, responses: List[Dict], request: str = None, **kwargs) -> Tuple[int, int]:
        """Token counts for throughput. Rerank has no completion tokens."""
        try:
            last = responses[-1] if responses else {}
            if isinstance(last, dict) and last.get('usage'):
                usage = last['usage']
                return (usage.get('prompt_tokens') or usage.get('total_tokens', 0)), 0
            # Fallback: estimate from the request (query + documents).
            if self.tokenizer and request:
                req = json.loads(request)
                texts = [req.get('query', '')] + list(req.get('documents', []))
                total = sum(len(self.tokenizer.encode(t, add_special_tokens=False)) for t in texts if t)
                return total, 0
            return 0, 0
        except Exception as e:
            logger.error(f'Failed to parse rerank response: {e}. Response: {responses}')
            return 0, 0

    async def process_request(self, client_session, url: str, headers: Dict, body: Dict) -> BenchmarkData:
        """Single non-streaming POST (rerank is one request/response)."""
        headers = {'Content-Type': 'application/json', **headers}
        data = json.dumps(body, ensure_ascii=False)

        output = BenchmarkData()
        st = time.perf_counter()
        output.start_time = st
        output.request = data
        try:
            async with client_session.post(url=url, data=data, headers=headers) as response:
                ts = time.perf_counter()
                output.completed_time = ts
                output.query_latency = ts - st
                output.first_chunk_latency = output.query_latency

                if response.status == 200:
                    try:
                        payload = await response.json()
                    except Exception:
                        payload = await response.text()
                    if isinstance(payload, dict):
                        results = payload.get('results', [])
                        if results:
                            output.generated_text = f'num_results={len(results)}'
                        if usage := payload.get('usage'):
                            output.prompt_tokens = usage.get('prompt_tokens') or usage.get('total_tokens', 0)
                            output.completion_tokens = 0
                        output.response_messages.append(payload)
                    else:
                        output.generated_text = str(payload)
                    output.success = True
                else:
                    output.status_code = response.status
                    try:
                        output.error = json.dumps(await response.json(), ensure_ascii=False)
                    except Exception:
                        try:
                            output.error = await response.text()
                        except Exception:
                            output.error = response.reason or ''
                    output.success = False
        except Exception:
            output.success = False
            output.error = ''.join(traceback.format_exception(*sys.exc_info()))
            logger.error(output.error)
        return output
