# BlockHost Flutter Client (minimal)

Minimal Flutter UI to test the backend:
- Signup / Login
- Create server
- List servers + server detail

## Run

From repo root:
- `cd flutter_client`
- `flutter pub get`
- `flutter run --dart-define=BLOCKHOST_API_BASE_URL=http://YOUR_LAPTOP_LAN_IP:8000`

## Backend URL

Default base URL is `http://localhost:8000`. Override it at build/run time with
`--dart-define=BLOCKHOST_API_BASE_URL=...`.

Notes:
- Android emulator: use `http://10.0.2.2:8000`
- iOS simulator: `http://localhost:8000`
- Physical device: use your machine LAN IP (e.g. `http://192.168.1.50:8000`)
