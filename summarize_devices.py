import json
import logging
from tuya_discovery_generator import initialize_scanners

# Disable verbose logging to see only the summary
logging.getLogger("tuya_discovery").setLevel(logging.WARNING)

def main():
    # initialize_scanners now returns a DiscoveryGenerator instance
    gen = initialize_scanners()
    
    devices_path = "tuyadevices.json"
    try:
        with open(devices_path, 'r') as f:
            devices = json.load(f)
            
        print(f"{'Device ID':<30} | {'Name':<20} | {'Cat':<5} | {'Product ID':<20} | {'Source':<20}")
        print("-" * 110)
        
        stats = {}
        
        for dev in devices:
            # gen.generate(dev) returns (payloads, source)
            _, source = gen.generate(dev)
            print(f"{dev.get('id'):<30} | {dev.get('name', 'N/A')[:20]:<20} | {dev.get('category', 'N/A'):<5} | {dev.get('product_id'):<20} | {source:<20}")
            stats[source] = stats.get(source, 0) + 1
            
        print("-" * 110)
        print("Summary Statistics:")
        for k, v in stats.items():
            print(f"- {k}: {v}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
