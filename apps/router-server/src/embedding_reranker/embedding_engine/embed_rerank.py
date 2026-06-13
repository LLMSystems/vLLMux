import os

import torch
from transformers import (AutoModel, AutoModelForSequenceClassification,
                          AutoTokenizer)

from src.embedding_reranker.embedding_engine.baseinferencer import \
    BaseInferencer


class Reranker(BaseInferencer):
    def load_model(self, model_path):
        self.model = AutoModelForSequenceClassification.from_pretrained(model_path)
        self.model.eval()
        self.model.to(self.device)
    
    def rerank(self, query, documents, batch_size=8): 
        all_scores = []
        all_documents = []
        
        for i in range(0, len(documents), batch_size):
            batch_docs = documents[i:i+batch_size]
            pairs = [[query, doc] for doc in batch_docs]
            
            with torch.no_grad():
                inputs = self.tokenizer(pairs, padding=True, truncation=True, return_tensors='pt', max_length=self.max_length)
                inputs = {k: v.to(self.device) for k, v in inputs.items()}
                scores = self.model(**inputs, return_dict=True).logits.view(-1, ).float()
                
                if self.device.type == 'cuda':
                    scores = scores.cpu()
                scores = scores.numpy()
                
                all_scores.extend(scores)
                all_documents.extend(batch_docs)
                
                del inputs, scores
                torch.cuda.empty_cache()
        return all_scores
    

class Embedder(BaseInferencer):
    def load_model(self, model_path):
        self.model = AutoModel.from_pretrained(model_path)
        self.model.eval()
        self.model.to(self.device)
        
    def get_embeddings(self, texts, batch_size=8):
        all_embeddings = []
        if not isinstance(texts, list):
            texts = [texts]
        for  i in range(0, len(texts), batch_size):
            self.logger.debug(f"[Embedder] model: {self.model_name} embedding {len(texts)} texts")
            batch_texts = texts[i:i+batch_size]
            encoded_input = self.tokenizer(batch_texts, padding=True, truncation=True, return_tensors='pt', max_length=self.max_length)
            encoded_input = {k: v.to(self.device) for k, v in encoded_input.items()}
            
            with torch.no_grad():
                outputs = self.model(**encoded_input)
                embeddings = outputs.last_hidden_state[:, 0, :]
                all_embeddings.append(embeddings.cpu())
            
            del outputs, embeddings, encoded_input
            torch.cuda.empty_cache()
            
        final_embeddings = torch.cat(all_embeddings, dim=0)
        
        return final_embeddings.numpy()
    
    
class JinaEmbedder:
    def __init__(self, model_name, model_path, tokenizer_path, use_gpu=False, use_float16=False, max_length=512, logger=None):
        self.model_name = model_name
        self.model = AutoModel.from_pretrained(
            self.model_name,
            trust_remote_code=True,
            cache_dir=model_path
        )
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_name,
            trust_remote_code=True,
            cache_dir=tokenizer_path
        )
        self.use_float16 = use_float16
        assert max_length <= self.tokenizer.model_max_length, f"max_length should be less than or equal to {self.tokenizer.model_max_length}"
        self.max_length = max_length
        self.logger = logger
        self.device = torch.device("cuda" if use_gpu and torch.cuda.is_available() else "cpu")
        self.model.eval()
        self.model.to(self.device)
        
        if self.use_float16 and self.device.type == 'cuda':
            self.model = self.model.half()
            self.logger.info(f'[JinaEmbedder] {model_name} using float16')

        self.logger.info(f'[JinaEmbedder] {model_name} loaded on {self.device.type.upper()}')
    
    def get_embeddings(self, texts):
        """
        only support single text input, because of jina model
        """
        all_embeddings = []
        if not isinstance(texts, list):
            texts = [texts]
        for i in range(len(texts)):
            encoded_input = self.tokenizer(texts[i], padding=True, truncation=True, return_tensors='pt', max_length=self.max_length)
            encoded_input = {k: v.to(self.device) for k, v in encoded_input.items()}
            
            with torch.no_grad():
                outputs = self.model(**encoded_input)
                embeddings = outputs.last_hidden_state
                all_embeddings.append(embeddings.cpu().numpy()[0])
            
            del outputs, embeddings, encoded_input
            torch.cuda.empty_cache()
        return all_embeddings    

    
class OnnxEmbedder():
    def __init__(self, model_name, model_path, tokenizer_path, max_length=512, logger=None):
        self.model_name = model_name
        self.logger = logger
        self.model_path = model_path
        self.tokenizer_path = tokenizer_path
        self.load_model(model_path)
        self.load_tokenizer(tokenizer_path)
        self.logger.info(f'[BaseInferencer] config max_length: {max_length}')
        self.logger.info(f'[BaseInferencer] tokenizer max_length: {self.tokenizer.model_max_length}')
        assert max_length <= self.tokenizer.model_max_length, f"max_length : {self.max_length} should be less than or equal to {self.tokenizer.model_max_length}"
        self.max_length = max_length
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        
    def load_model(self, model_path):
        onnx_file = self.search_onnxfile(model_path, ".onnx")
        if onnx_file:
            try:
                from optimum.onnxruntime import ORTModelForFeatureExtraction
                self.logger.info(f"[Embedder] Loading ORT model {onnx_file}")
                self.model = ORTModelForFeatureExtraction.from_pretrained(
                    model_id=model_path,
                    file_name=onnx_file,
                    provider="CUDAExecutionProvider",
                )
            except Exception as e:
                raise RuntimeError(f"[Embedder] Error loading ORT model {onnx_file} : {e}")
        else:
            raise FileNotFoundError(f"[Embedder] No onnx file found in {model_path}")
        
    def load_tokenizer(self, tokenizer_path):
        self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)
        
    def search_onnxfile(self, path, extension=".onnx"):
        if not os.path.isdir(path):
            raise NotADirectoryError(f"The specified path is not a directory: {path}")
        
        files = os.listdir(path)
        for file in files:
            if file.endswith(extension):
                return file
        return None
    
    def get_embeddings(self, texts, batch_size=16):
        all_embeddings = []
        if not isinstance(texts, list):
            texts = [texts]
        for  i in range(0, len(texts), batch_size):
            self.logger.debug(f"[Embedder] model: {self.model_name} embedding {len(texts)} texts")
            batch_texts = texts[i:i+batch_size]
            encoded_input = self.tokenizer(batch_texts, padding=True, truncation=True, return_tensors='pt', max_length=self.max_length)
            encoded_input = {k: v.to(self.device) for k, v in encoded_input.items()}
            
            with torch.no_grad():
                outputs = self.model(**encoded_input)
                embeddings = outputs.last_hidden_state[:, 0, :]
                all_embeddings.append(embeddings.cpu())
            
            del outputs, embeddings, encoded_input
            torch.cuda.empty_cache()
            
        final_embeddings = torch.cat(all_embeddings, dim=0)
        
        return final_embeddings.numpy()


class OnnxReranker():
    def __init__(self, model_name, model_path, tokenizer_path, max_length=512, logger=None):
        self.model_name = model_name
        self.logger = logger
        self.model_path = model_path
        self.tokenizer_path = tokenizer_path
        self.load_model(model_path)
        self.load_tokenizer(tokenizer_path)
        self.logger.info(f'[BaseInferencer] config max_length: {max_length}')
        self.logger.info(f'[BaseInferencer] tokenizer max_length: {self.tokenizer.model_max_length}')
        assert max_length <= self.tokenizer.model_max_length, f"max_length : {self.max_length} should be less than or equal to {self.tokenizer.model_max_length}"
        self.max_length = max_length
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    
    def load_model(self, model_path):
        onnx_file = self.search_onnxfile(model_path, ".onnx")
        if onnx_file:
            try:
                from optimum.onnxruntime import \
                    ORTModelForSequenceClassification
                self.logger.info(f"[Embedder] Loading ORT model {onnx_file}")
                self.model = ORTModelForSequenceClassification.from_pretrained(
                    model_id=model_path,
                    file_name=onnx_file,
                    provider="CUDAExecutionProvider",
                )
            except Exception as e:
                raise RuntimeError(f"[Embedder] Error loading ORT model {onnx_file} : {e}")
        else:
            raise FileNotFoundError(f"[Embedder] No onnx file found in {model_path}")
        
    def load_tokenizer(self, tokenizer_path):
        self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)
        
    def search_onnxfile(self, path, extension=".onnx"):
        if not os.path.isdir(path):
            raise NotADirectoryError(f"The specified path is not a directory: {path}")
        
        files = os.listdir(path)
        for file in files:
            if file.endswith(extension):
                return file
        return None
    
    def rerank(self, query, documents, batch_size=16): 
        all_scores = []
        all_documents = []
        
        for i in range(0, len(documents), batch_size):
            batch_docs = documents[i:i+batch_size]
            pairs = [[query, doc] for doc in batch_docs]
            
            with torch.no_grad():
                inputs = self.tokenizer(pairs, padding=True, truncation=True, return_tensors='pt', max_length=self.max_length)
                inputs = {k: v.to(self.device) for k, v in inputs.items()}
                scores = self.model(**inputs, return_dict=True).logits.view(-1, ).float()
                
                if self.device.type == 'cuda':
                    scores = scores.cpu()
                scores = scores.numpy()
                
                all_scores.extend(scores)
                all_documents.extend(batch_docs)
                
                del inputs, scores
                torch.cuda.empty_cache()
        return all_scores