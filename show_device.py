import json
import sys
import logging
from tuya_discovery_generator import initialize_generator

# Disable verbose logging to see only the output
logging.getLogger("tuya_discovery").setLevel(logging.WARNING)
logging.getLogger("external_converter").setLevel(logging.WARNING)

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 show_device.py <index>")
        print("Example: python3 show_device.py 0")
        return

    try:
        index = int(sys.argv[1])
    except ValueError:
        print("Error: Index must be an integer.")
        return

    # Initialize generator
    gen = initialize_generator()
    
    devices_path = "tuyadevices.json"
    try:
        with open(devices_path, 'r') as f:
            devices = json.load(f)
        
        if index < 0 or index >= len(devices):
            print(f"Error: Index {index} is out of range (0 to {len(devices)-1}).")
            return

        device = devices[index]
        payloads, source = gen.generate(device)

        print("\n" + "="*80)
        print(f"DEVICE INFO (Index: {index})")
        print(f"  Name: {device.get('name', 'N/A')}")
        print(f"  ID:   {device.get('id')}")
        print(f"  PID:  {device.get('product_id')}")
        print(f"  Cat:  {device.get('category')}")
        print(f"  Match Source: {source}")
        print("="*80)
        print("\n--- MQTT DISCOVERY PAYLOADS ---")
        print(json.dumps(payloads, indent=2))
        print("="*80)

    except FileNotFoundError:
        print(f"Error: {devices_path} not found.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    main()
