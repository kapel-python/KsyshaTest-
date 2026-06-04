import urllib.request
import urllib.error
import json

def main():
    try:
        req = urllib.request.Request(
            'http://127.0.0.1:25086/api/admin/challenge/init', 
            data=json.dumps({'fingerprint': 'test_fp3'}).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'X-Forwarded-For': '192.168.1.101'}
        )
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
            print("Init:", data)
            token = data.get('token')
            if not token:
                return
    except urllib.error.URLError as e:
        print("Init failed:", e)
        return

    for i in range(10):
        print(f"Attempt {i+1}...")
        try:
            req = urllib.request.Request(
                'http://127.0.0.1:25086/api/admin/challenge/verify', 
                data=json.dumps({'token': token, 'password': 'wrong'}).encode('utf-8'),
                headers={'Content-Type': 'application/json', 'X-Forwarded-For': '192.168.1.101'}
            )
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode('utf-8'))
                print("Verify:", response.status, data)
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read().decode('utf-8'))
                print("Verify Error:", e.status, data)
                if data.get('error') == 'locked':
                    print("Locked successfully!")
                    break
            except Exception as ex:
                print("Failed to read error body:", ex)

if __name__ == '__main__':
    main()
