#!/usr/bin/env python3
"""
Integration Test Script for Techgium POC
Tests network discovery service and hardware ML monitor integration
"""

import sys
import os
from pathlib import Path

# Add paths in the correct order - new_work before hardware to avoid import conflicts
NEW_WORK_ROOT = Path(__file__).parent / "new_work"
ROOT = Path(__file__).parent / "poc Guardient"
HARDWARE_ROOT = NEW_WORK_ROOT / "hardware"

# Remove any existing paths and add in correct order
sys.path = [str(p) for p in sys.path if 'Techgium' not in p]
sys.path.insert(0, str(NEW_WORK_ROOT))
sys.path.insert(0, str(ROOT))

def test_imports():
    """Test that all imports work correctly."""
    print("\n[TEST] Testing imports...")
    
    # Import network ML specifically from new_work
    import importlib.util
    spec = importlib.util.spec_from_file_location("network_app", NEW_WORK_ROOT / "app.py")
    network_app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(network_app)
    
    try:
        Flow = network_app.Flow
        SEQ_LEN = network_app.SEQ_LEN
        FEATURE_SIZE = network_app.FEATURE_SIZE
        build_feature_vector = network_app.build_feature_vector
        print("  ✓ Network ML imports successful")
        print(f"    - SEQ_LEN: {SEQ_LEN}")
        print(f"    - FEATURE_SIZE: {FEATURE_SIZE}")
    except Exception as e:
        print(f"  ✗ Network ML import failed: {e}")
        return False
    
    try:
        # Import hardware components
        sys.path.insert(0, str(HARDWARE_ROOT))
        
        # Import hardware modules from their location
        spec_hw = importlib.util.spec_from_file_location("hw_features", HARDWARE_ROOT / "features.py")
        hw_features = importlib.util.module_from_spec(spec_hw)
        spec_hw.loader.exec_module(hw_features)
        
        spec_col = importlib.util.spec_from_file_location("hw_collector", HARDWARE_ROOT / "collector.py")
        hw_collector = importlib.util.module_from_spec(spec_col)
        spec_col.loader.exec_module(hw_collector)
        
        print("  ✓ Hardware ML imports successful")
        print(f"    - Hardware components loaded: features, collector")
    except Exception as e:
        print(f"  ✗ Hardware ML import failed: {e}")
        return False
    
    return True

def test_feature_extraction():
    """Test feature extraction for network flows."""
    print("\n[TEST] Testing network feature extraction...")
    
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("network_app", NEW_WORK_ROOT / "app.py")
        network_app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(network_app)
        
        Flow = network_app.Flow
        build_feature_vector = network_app.build_feature_vector
        
        # Create sample flows
        flows = [
            Flow(
                device_ip="192.168.1.10",
                remote_ip="8.8.8.8",
                device_mac="aa:bb:cc:dd:ee:ff",
                device_port="54321",
                remote_port="443",
                bytes_sent=10000,
                bytes_received=20000,
                packet_count=100,
                dns_query=None,
                last_seen=1.0
            )
        ]
        
        features = build_feature_vector(flows)
        print(f"  ✓ Feature extraction successful")
        print(f"    - Features shape: {features.shape}")
        print(f"    - Feature values: {features[:5]}...")
    except Exception as e:
        print(f"  ✗ Feature extraction failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

def test_hardware_features():
    """Test hardware feature computation."""
    print("\n[TEST] Testing hardware feature extraction...")
    
    try:
        sys.path.insert(0, str(HARDWARE_ROOT))
        from features import compute_and_reset
        from state import state
        
        # Simulate some data in state
        for i in range(5):
            state.cpu_samples.append(25.0 + i)
            state.mem_samples.append(50.0 + i)
            state.load_samples.append(1.0)
            state.disk_io_samples.append(1000000.0 + i*100)
            state.process_count_samples.append(100 + i)
            state.net_samples.append((1000 + i*100, 2000 + i*100))
        
        metrics = compute_and_reset()
        print(f"  ✓ Hardware feature extraction successful")
        print(f"    - Features computed: {len(metrics)}")
        print(f"    - Sample metrics: cpu_avg={metrics.get('cpu_avg', 0):.2f}, "
              f"memory_percent={metrics.get('memory_percent', 0):.2f}")
    except Exception as e:
        print(f"  ✗ Hardware feature extraction failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

def test_model_creation():
    """Test LSTM model creation."""
    print("\n[TEST] Testing network LSTM model creation...")
    
    try:
        import importlib.util
        import torch
        import numpy as np
        
        spec = importlib.util.spec_from_file_location("network_app", NEW_WORK_ROOT / "app.py")
        network_app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(network_app)
        
        create_new_model = network_app.create_new_model
        FEATURE_SIZE = network_app.FEATURE_SIZE
        
        model = create_new_model()
        print(f"  ✓ Model creation successful")
        print(f"    - Model type: {type(model).__name__}")
        
        # Test forward pass
        test_input = torch.randn(1, 100, FEATURE_SIZE)  # batch_size=1, seq_len=100, features=10
        with torch.no_grad():
            output = model(test_input)
        print(f"    - Forward pass successful")
        print(f"    - Input shape: {test_input.shape}")
        print(f"    - Output shape: {output.shape}")
    except Exception as e:
        print(f"  ✗ Model creation failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

def test_network_discovery_integration():
    """Test network discovery service integration."""
    print("\n[TEST] Testing network discovery service integration...")
    
    try:
        import importlib.util
        from collections import deque
        
        spec = importlib.util.spec_from_file_location("network_app", NEW_WORK_ROOT / "app.py")
        network_app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(network_app)
        
        Flow = network_app.Flow
        SEQ_LEN = network_app.SEQ_LEN
        build_feature_vector = network_app.build_feature_vector
        device_buffers = network_app.device_buffers
        
        # Test buffer creation
        mac = "aa:bb:cc:dd:ee:ff"
        buffer = device_buffers[mac]
        
        print(f"  ✓ Device buffer auto-creation successful")
        print(f"    - Buffer type: {type(buffer).__name__}")
        print(f"    - Buffer maxlen: {buffer.maxlen}")
        
        # Test appending features
        features = build_feature_vector([Flow(
            device_ip="192.168.1.10",
            remote_ip="8.8.8.8",
            device_mac=mac,
            device_port="54321",
            remote_port="443",
            bytes_sent=10000,
            bytes_received=20000,
            packet_count=100,
            dns_query=None,
            last_seen=1.0
        )])
        
        buffer.append(features)
        print(f"    - Feature appended successfully")
        print(f"    - Buffer size: {len(buffer)}")
        
    except Exception as e:
        print(f"  ✗ Network discovery integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

def main():
    """Run all tests."""
    print("=" * 60)
    print("Techgium POC Integration Tests")
    print("=" * 60)
    
    tests = [
        ("Imports", test_imports),
        ("Network Feature Extraction", test_feature_extraction),
        ("Hardware Feature Extraction", test_hardware_features),
        ("LSTM Model Creation", test_model_creation),
        ("Network Discovery Integration", test_network_discovery_integration),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n[ERROR] Test '{name}' crashed: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
    
    print("\n" + "=" * 60)
    print("Test Results Summary")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All integration tests passed!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
