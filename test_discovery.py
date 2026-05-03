import json
import logging
from tuya_discovery_generator import initialize_scanners

# Setup logging
logging.basicConfig(level=logging.INFO)

def main():
    gen = initialize_scanners()
    
    devices_path = "tuyadevices.json"
    try:
        with open(devices_path, 'r') as f:
            devices = json.load(f)
            
        # Test Kids Curtain exclusively
        target_name = 'kids_curtain_outside'
        sample = next((d for d in devices if d.get('name') == target_name), None)
        
        if sample:
            payloads, source = gen.generate(sample)
            print(f"\n--- Discovery Payloads for {target_name} (Matched via: {source}) ---")
            print(json.dumps(payloads, indent=2))
        else:
            print(f"Device {target_name} not found.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
