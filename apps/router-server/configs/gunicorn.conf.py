# gunicorn.conf.py

import os

bind = "0.0.0.0:8887"
workers = 1
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 0
loglevel = "info"
accesslog = "-"  
errorlog = "-"   
preload_app = False  
