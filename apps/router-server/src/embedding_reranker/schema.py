from typing import List, Optional, Union

from pydantic import BaseModel


class EmbeddingRequest(BaseModel):
    input: Union[str, List[str]] # input text or list of input texts
    model: Optional[str] = None # model name
    encoding_format: Optional[str] = "float" # encoding format, default is float
    query: Optional[str] = None # query string