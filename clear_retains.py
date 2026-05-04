import json
import sys
import time
import paho.mqtt.client as mqtt

# Configuration
BROKER_HOST = "localhost"
BROKER_PORT = 1883
TOPIC_PREFIX = "homeassistant"

def main():
    """
    MQTT 리테인 메시지 삭제 스크립트
    tuyadevices.json의 인덱스를 입력받아 해당 기기의 Home Assistant 리테인 메시지를 삭제합니다.
    """
    if len(sys.argv) < 2:
        print("Usage: python3 clear_retains.py <index>")
        print("Example: python3 clear_retains.py 0")
        return

    try:
        index = int(sys.argv[1])
    except ValueError:
        print("❌ 오류: 인덱스는 정수여야 합니다.")
        return

    devices_path = "tuyadevices.json"
    try:
        with open(devices_path, "r", encoding="utf-8") as f:
            devices = json.load(f)

        if index < 0 or index >= len(devices):
            print(f"❌ 오류: 인덱스 {index}가 범위를 벗어났습니다. (0 ~ {len(devices)-1})")
            return

        device = devices[index]
        device_id = device.get("id")
        device_name = device.get("name", "Unknown")

        if not device_id:
            print(f"❌ 오류: 인덱스 {index}의 기기에 ID가 없습니다.")
            return

        print(f"🧹 기기 리테인 메시지 삭제 중: {device_name} ({device_id})")
        print(f"🔍 필터 접두사: {TOPIC_PREFIX}")

        client = mqtt.Client()

        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                print("💡 MQTT 브로커에 연결되었습니다.")
                # homeassistant/# 토픽을 구독하여 리테인된 설정 메시지들을 수집합니다.
                client.subscribe(f"{TOPIC_PREFIX}/#")
            else:
                print(f"❌ 연결 실패, 반환 코드: {rc}")

        def on_message(client, userdata, msg):
            # 리테인된 메시지이고, 토픽에 기기 ID가 포함되어 있는 경우에만 처리
            if msg.retain:
                if device_id in msg.topic:
                    print(f"🗑️ 삭제 중: {msg.topic}")
                    # 페이로드를 비우고 retain=True로 발행하여 리테인 메시지 삭제
                    client.publish(msg.topic, payload="", retain=True)

        client.on_connect = on_connect
        client.on_message = on_message

        try:
            client.connect(BROKER_HOST, BROKER_PORT, 60)
            client.loop_start()

            print("🔄 리테인 메시지를 수신하고 삭제하는 중입니다. 3초간 대기합니다...")
            time.sleep(3)

            client.loop_stop()
            client.disconnect()
            print("✅ 작업이 완료되었습니다.")

        except ConnectionRefusedError:
            print(f"❌ '{BROKER_HOST}'에서 MQTT 브로커를 찾을 수 없습니다.")

    except FileNotFoundError:
        print(f"❌ '{devices_path}' 파일을 찾을 수 없습니다.")
    except Exception as e:
        print(f"❌ 예외 발생: {e}")

if __name__ == "__main__":
    main()
