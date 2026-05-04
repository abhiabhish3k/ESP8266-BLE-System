# ESP8266 BLE Attendance System

A distributed classroom attendance system using **ESP8266 (NodeMCU)** as the WiFi server and an **Android app** to scan nearby Bluetooth Low Energy (BLE) devices worn or carried by students.

```
┌──────────────┐    BLE scan     ┌────────────────┐    HTTP POST /scan   ┌─────────────────┐
│   Students   │ ─────────────►  │  Android App   │ ──────────────────►  │ ESP8266 Server  │
│ (BLE device) │                 │ (BLE Scanner)  │                      │  (HTTP + SPIFFS)│
└──────────────┘                 └────────────────┘                      └────────┬────────┘
                                                                                  │  serves
                                                                         ┌────────▼────────┐
                                                                         │ Web Dashboard   │
                                                                         │  (HTML + JS)    │
                                                                         └─────────────────┘
```

---

## Repository Structure

```
ESP8266-BLE-System/
├── esp8266/
│   ├── platformio.ini          # PlatformIO build config
│   ├── main/
│   │   ├── main.ino            # Entry point (setup + loop)
│   │   ├── storage.h/.cpp      # LittleFS JSON persistence
│   │   ├── attendance.h/.cpp   # In-memory attendance engine
│   │   └── api_handlers.h/.cpp # ESPAsyncWebServer route handlers
│   └── data/
│       └── index.html          # Web dashboard (served from LittleFS)
├── android/
│   ├── build.gradle
│   ├── settings.gradle
│   └── app/
│       ├── build.gradle
│       └── src/main/
│           ├── AndroidManifest.xml
│           └── java/com/attendance/ble/
│               ├── BLEScanner.kt    # BLE scanning + deduplication
│               ├── ApiClient.kt     # OkHttp REST client
│               └── MainActivity.kt  # UI + orchestration
└── ble_advertiser/
    ├── advertiser.py            # Python BLE beacon advertiser (student device)
    └── requirements.txt         # pip dependencies (bless)
```

---

## System Flow

```
1. Teacher opens web dashboard (http://<ESP8266-IP>)
2. Teacher clicks "Start Session"
3. Android app (running on teacher's/assistant's phone) starts scanning BLE
4. App detects nearby students' BLE devices
5. App sends detected devices to ESP8266 every 5 seconds (POST /scan)
6. ESP8266 marks students present (deduplicates per session)
7. Dashboard auto-refreshes and shows live attendance
8. Teacher clicks "Stop Session" when class ends
```

---

## ESP8266 Backend

### Requirements

| Tool | Version |
|------|---------|
| PlatformIO Core | ≥ 6.x |
| ESP8266 Arduino Core | ≥ 3.1.0 |
| ESPAsyncWebServer | ≥ 1.2.3 |
| ArduinoJson | ≥ 6.21 |

### Configuration (`main.ino`)

Edit these constants before flashing:

```cpp
#define WIFI_SSID       "YOUR_SSID"
#define WIFI_PASSWORD   "YOUR_PASSWORD"
#define AP_SSID         "AttendAP"       // fallback AP SSID
#define AP_PASSWORD     "attend123"
#define SESSION_TIMEOUT_S  3600UL        // auto-stop session after 1 hour (0 = off)
```

Edit authentication token in `api_handlers.h`:

```cpp
#define AUTH_TOKEN "esp8266-attend-secret"  // set "" to disable auth
```

### Build & Flash

```bash
cd esp8266
pio run                          # compile
pio run --target upload          # upload firmware
pio run --target uploadfs        # upload LittleFS (web dashboard)
pio device monitor               # open serial monitor
```

### REST API

All mutation endpoints require `Authorization: Bearer <AUTH_TOKEN>` header.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/scan` | Receive BLE device scan from Android |
| `POST` | `/register` | Register a student |
| `GET` | `/students` | List all students |
| `GET` | `/attendance` | List all attendance records |
| `POST` | `/start-session` | Start an attendance session |
| `POST` | `/stop-session` | Stop the current session |
| `GET` | `/session` | Get current session status |

#### POST /scan

```json
{
  "device_id": "AA:BB:CC:DD:EE:FF",
  "rssi": -65,
  "timestamp": 1710000000
}
```

Response:
```json
{ "marked": true }
```

#### POST /register

```json
{
  "name": "John Doe",
  "device_id": "AA:BB:CC:DD:EE:FF"
}
```

#### GET /attendance

```json
[
  { "device_id": "AA:BB:CC:DD:EE:FF", "session_id": "sess_1234", "timestamp": 1710000000, "name": "John Doe" }
]
```

### Storage

- **`/students.json`** – registered students
- **`/attendance.json`** – attendance records

Files are stored in LittleFS. Format: compact JSON arrays.

RAM budget:
- Students: max 50, ~4 KB JSON doc
- Attendance: max 200, ~16 KB JSON doc
- Total flash I/O only on mutation, not on every request

### WiFi Modes

1. **Station (STA)** – connects to your WiFi router. IP shown in serial monitor.
2. **AP fallback** – if STA fails, creates `AttendAP` hotspot. Connect to it, then open `http://192.168.4.1`.

---

## Android App

### Requirements

- Android Studio Hedgehog or later
- min SDK 23 (Android 6.0), target SDK 34
- Phone with Bluetooth LE support

### Configuration (`ApiClient.kt`)

```kotlin
ApiClient.baseUrl   = "http://192.168.1.XX"    // ESP8266 IP on your network
ApiClient.authToken = "esp8266-attend-secret"   // must match firmware
```

Or enter the IP from the app's UI at runtime.

### Build

Open `android/` in Android Studio → Run on device.

### Permissions

| Permission | Purpose |
|------------|---------|
| `BLUETOOTH_SCAN` (API 31+) | BLE scanning |
| `BLUETOOTH_CONNECT` (API 31+) | BLE device info |
| `BLUETOOTH` + `BLUETOOTH_ADMIN` (API ≤ 30) | BLE |
| `ACCESS_FINE_LOCATION` (API ≤ 30) | Required for BLE on older Android |
| `INTERNET` | HTTP to ESP8266 |

### Features

- **BLE Scanner** (`BLEScanner.kt`)
  - Continuous scan in `SCAN_MODE_BALANCED` mode
  - RSSI filter (default ≥ −80 dBm)
  - Deduplication via `ConcurrentHashMap`
  - Batch callback every 5 seconds
- **API Client** (`ApiClient.kt`)
  - OkHttp with 5 s connect / 10 s read timeout
  - Auto-retry on connection failure
  - All calls are coroutine-friendly suspend functions
- **MainActivity** (`MainActivity.kt`)
  - Enter ESP8266 IP, connect, start/stop scanning
  - Shows live device list with RSSI
  - Background reconnect loop every 10 s

---

## Python BLE Beacon Advertiser

Students who use a **laptop or Raspberry Pi** (anything running Python with a
Bluetooth adapter) can run `ble_advertiser/advertiser.py` instead of carrying
a dedicated BLE peripheral.

The script broadcasts a short, stable **Beacon ID** as the BLE local name.
The Android scanner (`BLEScanner.kt`) reads this name from the advertisement
packet and uses it as `device_id` — bypassing MAC address randomisation
entirely.

### Why this matters

Modern phones and many OS-level BLE stacks rotate the Bluetooth MAC address
periodically to prevent tracking.  A rotating MAC breaks the simple
string-equality match in the ESP8266.  A stable local name prefixed with
`ATT-` solves this without any change to the ESP8266 firmware.

### Requirements

| | |
|---|---|
| Python | ≥ 3.8 |
| OS | Linux (BlueZ ≥ 5.43), macOS 10.15+, Windows 10 1803+ |
| Hardware | Any Bluetooth 4.0+ adapter |

```bash
cd ble_advertiser
pip install -r requirements.txt
```

> **Linux note:** BlueZ advertising requires either `sudo` or a Bluetooth
> capability grant:
> ```bash
> sudo python advertiser.py --beacon-id ATT-john-doe
> # or, to avoid sudo permanently:
> sudo setcap 'cap_net_admin+eip' $(which python3)
> ```

### Usage

```bash
python advertiser.py --beacon-id ATT-<your-id>
```

**Beacon ID rules:**
- Must start with `ATT-`
- Suffix: letters, digits, `-`, `_` only (no spaces)
- Maximum 29 characters total
- Examples: `ATT-john-doe`, `ATT-S12345`, `ATT-alice_smith`

### Registration walkthrough

```
1. Run the script:
       python advertiser.py --beacon-id ATT-john-doe

   Output:
       ======================================================
         ESP8266 Attendance System – BLE Beacon Advertiser
       ======================================================
         Beacon ID  :  ATT-john-doe
         Register as:  device_id = ATT-john-doe
       ======================================================
       ✓  Advertising as:  ATT-john-doe

2. Open the ESP8266 web dashboard → Students tab
3. Register:
       Name      : John Doe
       Device ID : ATT-john-doe   ← exact string from step 1

4. Start an attendance session (dashboard → Session tab)
5. Keep the script running in the background during class
```

### How detection works

```
advertiser.py               Android BLEScanner          ESP8266
─────────────               ──────────────────          ───────
Advertises local            Reads scanRecord            Looks up deviceId
name = "ATT-john-doe"  ──►  .deviceName = "ATT-john-doe"   in students[]
                            Uses name as deviceId   ──► Marks student present
```

The `ATT-` prefix is the handshake between the Python advertiser and the
Android scanner.  Any device name without this prefix falls back to the
hardware MAC (for dedicated BLE peripherals).

---

## Web Dashboard

Served directly from ESP8266 LittleFS (`esp8266/data/index.html`).

Open in any browser: `http://<ESP8266-IP>/`

### Pages

| Tab | Description |
|-----|-------------|
| **Live Attendance** | Table of all present students, auto-refreshes every 5 s |
| **Students** | Register new students + list of all registered |
| **Session** | Start/Stop session + current session status |

### Auth

The dashboard sends `Authorization: Bearer esp8266-attend-secret` with every mutating request. Update `AUTH_TOKEN` in both `api_handlers.h` and the `<script>` block in `index.html`.

---

## Production Constraints Met

| Constraint | How addressed |
|-----------|---------------|
| ~40 KB usable RAM | `StaticJsonDocument` sizes tuned; max 50 students / 200 records |
| No blocking operations | ESPAsyncWebServer (fully async), `yield()` in loop() |
| Deduplication | Per-session device tracking in `attendance.cpp` |
| RSSI filtering | `minRssi` parameter in `scanDevice()` and `BLEScanner` |
| Session timeout | `checkTimeout()` called from `loop()` every second |
| Auth token | Bearer token checked on all mutation endpoints |
| Flash persistence | LittleFS JSON files, written only on mutations |
| Reconnect logic | Android reconnect loop every 10 s in `MainActivity` |

---

## Quick Start

1. **Register students** via web dashboard → Students tab
2. **Start session** via web dashboard → Session tab
3. **Open Android app**, enter ESP8266 IP, tap Connect, tap Start Scan
4. Students walk within BLE range of the Android device
5. Dashboard → Live Attendance shows who is present
6. **Stop session** when class ends
