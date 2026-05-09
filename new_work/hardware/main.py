# main.py
from fastapi import FastAPI
from features import compute_and_reset
import threading
from collector import collect

app = FastAPI()

@app.on_event("startup")
def start_collector():
    thread = threading.Thread(target=collect, daemon=True)
    thread.start()

@app.get("/metrics")
def get_metrics():
    return compute_and_reset()