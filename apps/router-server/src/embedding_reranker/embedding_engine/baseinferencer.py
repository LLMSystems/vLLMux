import torch
from transformers import AutoTokenizer


class BaseInferencer:
    def __init__(self, model_name, model_path, tokenizer_path, use_gpu=False, use_float16=False, max_length=512, logger=None):
        self.model_name = model_name
        self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)
        self.use_float16 = use_float16
        assert max_length <= self.tokenizer.model_max_length, f"max_length should be less than or equal to {self.tokenizer.model_max_length}"
        self.max_length = max_length
        self.logger = logger
        
        self.device = torch.device("cuda" if use_gpu and torch.cuda.is_available() else "cpu")
        
        self.load_model(model_path)
        
        if self.use_float16 and self.device.type == 'cuda':
            self.model = self.model.half()
            self.logger.info(f'[BaseInferencer] {model_name} using float16')

        self.logger.info(f'[BaseInferencer] {model_name} loaded on {self.device.type.upper()}')
        
    def load_model(self, model_path):
        raise NotImplementedError("Subclasses must implement this method to load the model")
    
    def calculate_memory(self):
        total_memory = torch.cuda.get_device_properties(0).total_memory / 1e6
        allocated_memory = torch.cuda.memory_allocated(0) / 1e6
        cached_memory = torch.cuda.memory_reserved(0) / 1e6

        self.logger.info(f"Total GPU Memory: {total_memory:.2f} MB")
        self.logger.info(f"Allocated Memory: {allocated_memory:.2f} MB")
        self.logger.info(f"Cached Memory: {cached_memory:.2f} MB")