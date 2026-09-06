import urllib.request
import urllib.error
import json

req = urllib.request.Request(
    'http://localhost:8000/api/servers',
    data=json.dumps({"flavor": "java", "world_name": "test", "config": {"java_version": "17"}}).encode('utf-8'),
    headers={
        'Content-Type': 'application/json',
        'Authorization': 'Bearer 8c2f634c0376a44b487ef856e47e3f42'
    }
)
try:
    response = urllib.request.urlopen(req)
    print(response.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print(f"HTTPError: {e.code} - {e.read().decode('utf-8')}")
