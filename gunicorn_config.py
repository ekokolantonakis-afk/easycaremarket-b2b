import os

port = os.environ.get('PORT', '8080')
bind = f"0.0.0.0:{port}"
workers = 1
worker_class = "sync"
timeout = 300
keepalive = 5
preload_app = True
