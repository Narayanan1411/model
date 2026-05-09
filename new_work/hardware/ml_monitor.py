import os
import sys
import time
import uuid
import json
import requests
import numpy as np
import torch
import torch.nn as nn
from sklearn.preprocessing import StandardScaler
import joblib
from collections import deque
from datetime import datetime, timezone
import psutil

# Add Guardient root to path for Kafka imports
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../poc Guardient"))
sys.path.insert(0, ROOT)

try:
    from pipeline.producer import publish_event
    from pipeline.topics import RAW_EVENTS
except ImportError:
    print("[HW-ML] Warning: Could not import Guardient pipeline modules.")
    publish_event = None

SEQ_LEN = 30
FEATURE_SIZE = 11
MODEL_DIR = "models"
os.makedirs(MODEL_DIR, exist_ok=True)

class LSTMAutoencoder(nn.Module):
    def __init__(self, input_size, hidden_size=64):
        super().__init__()
        self.encoder = nn.LSTM(input_size, hidden_size, batch_first=True)
        self.decoder = nn.LSTM(hidden_size, input_size, batch_first=True)

    def forward(self, x):
        encoded, (hidden, cell) = self.encoder(x)
        repeated = hidden.repeat(x.shape[1], 1, 1).permute(1, 0, 2)
        decoded, _ = self.decoder(repeated)
        return decoded

class HardwareMLMonitor:
    def __init__(self):
        self.buffer = deque(maxlen=SEQ_LEN)
        self.model = None
        self.scaler = None
        self.threshold = 0.0
        
        # Get primary MAC address for device ID
        self.mac = ':'.join(['{:02x}'.format((uuid.getnode() >> ele) & 0xff) for ele in range(0,8*6,8)][::-1])
        self.device_id = f"dev_{self.mac.replace(':', '')}"
        
        self.model_path = os.path.join(MODEL_DIR, f"{self.mac.replace(':', '_')}_hw.pt")
        self.scaler_path = os.path.join(MODEL_DIR, f"{self.mac.replace(':', '_')}_hw_scaler.pkl")

    def load_model(self):
        if os.path.exists(self.model_path) and os.path.exists(self.scaler_path):
            self.model = LSTMAutoencoder(FEATURE_SIZE)
            self.model.load_state_dict(torch.load(self.model_path))
            self.scaler = joblib.load(self.scaler_path)
            self.model.eval()
            print("[HW-ML] Loaded existing hardware model.")
            return True
        return False

    def save_model(self):
        torch.save(self.model.state_dict(), self.model_path)
        joblib.dump(self.scaler, self.scaler_path)

    def train_model(self):
        print(f"[HW-ML] Training Hardware LSTM model with {SEQ_LEN} sequences...")
        X = np.array([list(self.buffer)])
        self.scaler = StandardScaler()
        
        X_reshaped = X.reshape(-1, FEATURE_SIZE)
        self.scaler.fit(X_reshaped)
        X_scaled = self.scaler.transform(X_reshaped).reshape(X.shape)
        X_tensor = torch.tensor(X_scaled, dtype=torch.float32)

        self.model = LSTMAutoencoder(FEATURE_SIZE)
        optimizer = torch.optim.Adam(self.model.parameters(), lr=0.001)
        criterion = nn.MSELoss()

        self.model.train()
        for epoch in range(20):
            optimizer.zero_grad()
            output = self.model(X_tensor)
            loss = criterion(output, X_tensor)
            loss.backward()
            optimizer.step()

        self.model.eval()
        with torch.no_grad():
            recon = self.model(X_tensor)
            errors = torch.mean((recon - X_tensor) ** 2, dim=(1, 2)).numpy()

        self.threshold = float(errors.mean() + 3 * errors.std() + 0.1) # Add small epsilon
        self.save_model()
        print(f"[HW-ML] Training complete. Threshold: {self.threshold:.4f}")

    def detect_anomaly(self, sequence):
        if not self.model:
            return {"anomaly": False, "score": 0.0, "threshold": 0.0}

        X = np.array([sequence])
        X_scaled = self.scaler.transform(X.reshape(-1, FEATURE_SIZE)).reshape(X.shape)
        X_tensor = torch.tensor(X_scaled, dtype=torch.float32)

        with torch.no_grad():
            recon = self.model(X_tensor)
            error = torch.mean((recon - X_tensor) ** 2).item()

        return {
            "anomaly": error > self.threshold,
            "score": float(error),
            "threshold": float(self.threshold)
        }

    def run(self):
        print("[HW-ML] Starting Hardware ML Monitor...")
        self.load_model()
        
        feature_keys = [
            "cpu_avg", "cpu_spike", "memory_percent", "memory_spike",
            "load_avg_delta", "disk_io_rate", "disk_io_spike",
            "process_count", "new_process_rate", "bytes_out_server", "bytes_in_server"
        ]

        while True:
            try:
                resp = requests.get("http://localhost:8002/metrics", timeout=5)
                if resp.status_code == 200:
                    metrics = resp.json()
                    vector = [float(metrics.get(k, 0)) for k in feature_keys]
                    self.buffer.append(vector)

                    ml_result = {"anomaly": False, "score": 0.0}

                    if len(self.buffer) == SEQ_LEN:
                        if not self.model:
                            self.train_model()
                        else:
                            ml_result = self.detect_anomaly(list(self.buffer))
                            
                    print(f"[HW-ML] Metrics collected. Buffer: {len(self.buffer)}/{SEQ_LEN}. Anomaly Score: {ml_result['score']:.4f}")

                    # Publish to Guardient Pipeline
                    if publish_event:
                        event = {
                            "event_id": str(uuid.uuid4()),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "device_id": self.device_id,
                            "source": "hardware",
                            "collector": "hardware_lstm",
                            "features": metrics,
                            "ml_anomaly_score": ml_result["score"],
                            "ml_is_anomaly": ml_result["anomaly"],
                            "device_type": "server"
                        }
                        publish_event(RAW_EVENTS, event)

            except Exception as e:
                print(f"[HW-ML] Error polling metrics: {e}")
            
            time.sleep(5)

if __name__ == "__main__":
    monitor = HardwareMLMonitor()
    monitor.run()
