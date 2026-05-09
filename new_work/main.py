import subprocess
import json
import threading
import time
import psutil
import socket
from fastapi import FastAPI

app = FastAPI()

# --- Configuration ---
INTERFACE = "wlp1s0" 

def get_auto_mask(iface):
    """
    Dynamically detects the local network prefix (e.g., '192.168.1.').
    This ensures bytes_sent and bytes_received are correctly categorized.
    """
    try:
        addr_dict = psutil.net_if_addrs()
        if iface in addr_dict:
            for snic in addr_dict[iface]:
                if snic.family == socket.AF_INET: # IPv4
                    # Takes '192.168.0.5' and returns '192.168.0.'
                    return ".".join(snic.address.split(".")[:3]) + "."
    except Exception as e:
        print(f"Mask detection failed: {e}")
    return "192.168.1." # Static fallback

# Global configuration determined at runtime
LOCAL_IP_MASK = get_auto_mask(INTERFACE)
flows = {}
lock = threading.Lock()
last_query_time = time.time()

print(f"[*] Sniffer initialized on {INTERFACE}")
print(f"[*] Real-time Mask: {LOCAL_IP_MASK}")

def get_field(layers, field_name):
    val = layers.get(field_name)
    return val[0] if isinstance(val, list) and val else None

def parse_packet(packet):
    layers = packet.get("layers", {})
    src_ip = get_field(layers, "ip_src")
    dst_ip = get_field(layers, "ip_dst")
    
    if not src_ip or not dst_ip:
        return

    # Check direction against the dynamically detected mask
    is_src_local = src_ip.startswith(LOCAL_IP_MASK)
    is_dst_local = dst_ip.startswith(LOCAL_IP_MASK)

    # Only process flows involving the local subnet
    if not is_src_local and not is_dst_local:
        return

    pkt_len = int(get_field(layers, "frame_len") or "0")
    src_port = get_field(layers, "tcp_srcport") or get_field(layers, "udp_srcport") or "0"
    dst_port = get_field(layers, "tcp_dstport") or get_field(layers, "udp_dstport") or "0"

    # Normalized Key for Bidirectional Tracking
    p1, p2 = (src_ip, src_port), (dst_ip, dst_port)
    flow_key = f"{p1}-{p2}" if p1 < p2 else f"{p2}-{p1}"

    with lock:
        if flow_key not in flows:
            flows[flow_key] = {
                "device_ip": src_ip if is_src_local else dst_ip,
                "remote_ip": dst_ip if is_src_local else src_ip,
                "device_mac": get_field(layers, "eth_src") if is_src_local else get_field(layers, "eth_dst"),
                "device_port": src_port if is_src_local else dst_port,
                "remote_port": dst_port if is_src_local else src_port,
                "bytes_sent": 0,
                "bytes_received": 0,
                "packet_count": 0,
                "dns_query": get_field(layers, "dns_qry_name"),
                "last_seen": time.time()
            }
        
        f = flows[flow_key]
        
        # Categorize bytes based on the Mask
        if is_src_local:
            f["bytes_sent"] += pkt_len
        else:
            f["bytes_received"] += pkt_len
            
        f["packet_count"] += 1
        f["last_seen"] = time.time()

def start_tshark():
    cmd = [
        "tshark", "-i", INTERFACE, "-p", "-n", "-l", "-T", "ek",
        "-e", "eth.src", "-e", "eth.dst",
        "-e", "ip.src", "-e", "ip.dst",
        "-e", "tcp.srcport", "-e", "tcp.dstport",
        "-e", "udp.srcport", "-e", "udp.dstport",
        "-e", "frame.len", "-e", "dns.qry.name"
    ]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
    for line in process.stdout:
        if '"layers"' not in line: continue
        try:
            parse_packet(json.loads(line))
        except: continue

@app.on_event("startup")
def startup():
    threading.Thread(target=start_tshark, daemon=True).start()

@app.get("/consume")
def consume():
    """
    Returns data captured since the last call and clears memory.
    Perfect for sequential time-steps in an LSTM model.
    """
    global last_query_time
    now = time.time()
    duration = now - last_query_time
    
    with lock:
        data = list(flows.values())
        flows.clear()
        last_query_time = now

    return {
        "metadata": {
            "timestamp": now,
            "duration": round(duration, 4),
            "flow_count": len(data),
            "mask_used": LOCAL_IP_MASK
        },
        "flows": data
    }