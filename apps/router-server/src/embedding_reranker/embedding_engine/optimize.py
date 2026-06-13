from optimum.onnxruntime import (ORTModelForFeatureExtraction,
                                 ORTModelForSequenceClassification,
                                 ORTOptimizer)
from optimum.onnxruntime.configuration import OptimizationConfig
from transformers import AutoTokenizer


def optimize_embedding_model(model_path, tokenizer_path, onnx_save_path, optimization_level=2, optimize_for_gpu=True, fp16=True):
    model = ORTModelForFeatureExtraction.from_pretrained(model_id=model_path, export=True)
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)

    optimizer = ORTOptimizer.from_pretrained(model)

    optimizer_config = OptimizationConfig(
        optimization_level=optimization_level,
        optimize_for_gpu=optimize_for_gpu,
        fp16=fp16
    )

    optimizer.optimize(save_dir=onnx_save_path, optimization_config=optimizer_config)
    
    tokenizer.save_pretrained(onnx_save_path)
    print(f"Embedding Model and tokenizer have been optimized and saved to {onnx_save_path}")

def optimize_rerank_model(model_path, tokenizer_path, onnx_save_path, optimization_level=2, optimize_for_gpu=True, fp16=True):
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)
    model = ORTModelForSequenceClassification.from_pretrained(model_id=model_path, export=True)
    
    optimizer = ORTOptimizer.from_pretrained(model)

    optimizer_config = OptimizationConfig(
        optimization_level=optimization_level,
        optimize_for_gpu=optimize_for_gpu,
        fp16=fp16
    )

    optimizer.optimize(save_dir=onnx_save_path, optimization_config=optimizer_config)
    
    tokenizer.save_pretrained(onnx_save_path)
    print(f"Rerank Model and tokenizer have been optimized and saved to {onnx_save_path}")