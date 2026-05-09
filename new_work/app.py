# app.py

from fastapi import FastAPI
from pydantic import BaseModel
from collections import defaultdict, deque
from typing import List, Optional
import numpy as np
import torch
import torch.nn as nn
from sklearn.preprocessing import StandardScaler
import joblib
import os
import time

SEQ_LEN = 100
FEATURE_SIZE = 10
MODEL_DIR = "models"

os.makedirs(MODEL_DIR, exist_ok=True)

app = FastAPI()

device_buffers = defaultdict(lambda: deque(maxlen=SEQ_LEN))
device_scalers = {}
device_thresholds = {}
device_models = {}

class Flow(BaseModel):
    device_ip: str
    remote_ip: str
    device_mac: str
    device_port: str
    remote_port: str
    bytes_sent: int
    bytes_received: int
    packet_count: int
    dns_query: Optional[str] = None
    last_seen: float

class LSTMAutoencoder(nn.Module):
    def __init__(self, input_size, hidden_size=64):
        super().__init__()

        self.encoder = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            batch_first=True
        )

        self.decoder = nn.LSTM(
            input_size=hidden_size,
            hidden_size=input_size,
            batch_first=True
        )

    def forward(self, x):
        encoded, (hidden, cell) = self.encoder(x)

        repeated = hidden.repeat(x.shape[1], 1, 1).permute(1, 0, 2)

        decoded, _ = self.decoder(repeated)

        return decoded

def build_feature_vector(flows: List[Flow]):

    total_bytes_sent = sum(f.bytes_sent for f in flows)
    total_bytes_received = sum(f.bytes_received for f in flows)

    total_packets = sum(f.packet_count for f in flows)

    unique_remote_ips = len(set(f.remote_ip for f in flows))

    unique_remote_ports = len(set(f.remote_port for f in flows))

    avg_packet_size = (
        (total_bytes_sent + total_bytes_received)
        / max(total_packets, 1)
    )

    dns_requests = sum(1 for f in flows if f.dns_query)

    tcp_ratio = 1.0
    udp_ratio = 0.0

    inbound_outbound_ratio = (
        total_bytes_received / max(total_bytes_sent, 1)
    )

    connection_rate = len(flows)

    return np.array([
        total_bytes_sent,
        total_bytes_received,
        total_packets,
        unique_remote_ips,
        unique_remote_ports,
        avg_packet_size,
        dns_requests,
        tcp_ratio,
        inbound_outbound_ratio,
        connection_rate
    ], dtype=np.float32)

def get_model_path(mac):
    return f"{MODEL_DIR}/{mac.replace(':', '_')}.pt"

def get_scaler_path(mac):
    return f"{MODEL_DIR}/{mac.replace(':', '_')}_scaler.pkl"

def create_new_model():
    model = LSTMAutoencoder(FEATURE_SIZE)
    return model

def save_model(mac, model, scaler):

    torch.save(model.state_dict(), get_model_path(mac))
    joblib.dump(scaler, get_scaler_path(mac))

def load_model(mac):

    model_path = get_model_path(mac)
    scaler_path = get_scaler_path(mac)

    if not os.path.exists(model_path):
        return None, None

    model = create_new_model()

    model.load_state_dict(
        torch.load(model_path)
    )

    scaler = joblib.load(scaler_path)

    model.eval()

    return model, scaler

def train_device_model(mac, sequences):

    X = np.array(sequences)

    scaler = StandardScaler()

    X_reshaped = X.reshape(-1, FEATURE_SIZE)

    scaler.fit(X_reshaped)

    X_scaled = scaler.transform(X_reshaped)

    X_scaled = X_scaled.reshape(X.shape)

    X_tensor = torch.tensor(X_scaled, dtype=torch.float32)

    model = create_new_model()

    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)

    criterion = nn.MSELoss()

    model.train()

    for epoch in range(20):

        optimizer.zero_grad()

        output = model(X_tensor)

        loss = criterion(output, X_tensor)

        loss.backward()

        optimizer.step()

    model.eval()

    with torch.no_grad():

        recon = model(X_tensor)

        errors = torch.mean(
            (recon - X_tensor) ** 2,
            dim=(1, 2)
        ).numpy()

    threshold = errors.mean() + 3 * errors.std()

    device_thresholds[mac] = threshold
    device_models[mac] = model
    device_scalers[mac] = scaler

    save_model(mac, model, scaler)

    print(f"[TRAINED] {mac}")
    print(f"Threshold: {threshold}")


def detect_anomaly(mac, sequence):

    model = device_models.get(mac)
    scaler = device_scalers.get(mac)

    if model is None:
        return {
            "status": "learning"
        }

    X = np.array(sequence)

    X_scaled = scaler.transform(X)

    X_tensor = torch.tensor(
        X_scaled[np.newaxis, :, :],
        dtype=torch.float32
    )

    with torch.no_grad():

        recon = model(X_tensor)

        error = torch.mean(
            (recon - X_tensor) ** 2
        ).item()

    threshold = device_thresholds[mac]

    return {
        "anomaly": error > threshold,
        "score": float(error),
        "threshold": float(threshold)
    }


@app.post("/ingest")
async def ingest(flows: List[Flow]):

    grouped = defaultdict(list)

    # GROUP FLOWS BY DEVICE

    for flow in flows:
        grouped[flow.device_mac].append(flow)

    results = {}

    for mac, device_flows in grouped.items():

        # BUILD FEATURE VECTOR

        feature_vector = build_feature_vector(device_flows)

        # STORE TEMPORAL DATA

        device_buffers[mac].append(feature_vector)

        # NOT ENOUGH DATA YET

        if len(device_buffers[mac]) < SEQ_LEN:

            results[mac] = {
                "status": "collecting_data",
                "buffer_size": len(device_buffers[mac])
            }

            continue

        # TRAIN MODEL IF FIRST TIME

        if mac not in device_models:

            sequences = []

            buffer_data = list(device_buffers[mac])

            sequences.append(buffer_data)

            train_device_model(mac, sequences)

            results[mac] = {
                "status": "model_trained"
            }

            continue

        # RUN DETECTION

        result = detect_anomaly(
            mac,
            list(device_buffers[mac])
        )

        results[mac] = result

    return results

