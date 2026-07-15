"""
sensor_bridge.py — connects the LEGO Education color sensor (CS&AI kit)
to the Brick Brain webpage. No wifi needed: Bluetooth + localhost only.

SETUP (once, with internet, before demo day):
    pip install legoeducation        (needs Python 3.14+)

RUN (works fully offline):
    python sensor_bridge.py
    -> enter your Connection Card color + serial when asked
    -> leave this window open, then open lego-color-classifier.html
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8735

try:
    import legoeducation as le
except ImportError:
    print("\n[!] The 'legoeducation' package is not installed.")
    print("    Run:  pip install legoeducation   (requires Python 3.14+)\n")
    raise SystemExit(1)

# ---------- connect to the color sensor ----------
print("Look at the Connection Card that came with your color sensor.")
card_color_name = input("Card color (e.g. AZURE, RED, YELLOW...): ").strip().upper()
card_serial = input("Card serial (e.g. 3683): ").strip()

const_name = f"LEGO_COLOR_{card_color_name}"
card_color = getattr(le, const_name, None)
if card_color is None:
    print(f"[!] Unknown color '{card_color_name}'. Check constants.md in the LEGO repo.")
    raise SystemExit(1)

print("Connecting to color sensor over Bluetooth (make sure it's on and blinking)...")
colorsensor = le.ColorSensor()
colorsensor.connect(card_color=card_color, card_serial=card_serial)

if not colorsensor.connected:
    print("[!] Could not connect. Is the sensor charged, on, and broadcasting?")
    raise SystemExit(1)

print("Sensor connected ✓")
try:
    colorsensor.light_color(le.LEGO_COLOR_GREEN)  # visual confirmation on the sensor
except Exception:
    pass

# ---------- tiny local web server ----------
def current_reading():
    s = colorsensor.sensor
    try:
        return {
            "r": int(s.rawRed),
            "g": int(s.rawGreen),
            "b": int(s.rawBlue),
            "reflection": getattr(s, "reflection", None),
            "color_name": str(getattr(s, "color", "")),
        }
    except Exception as e:
        return {"error": f"Could not read sensor: {e}"}

class Handler(BaseHTTPRequestHandler):
    def _send(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        # allow the file:// page to fetch from us
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/reading"):
            self._send(current_reading())
        elif self.path.startswith("/status"):
            self._send({"ok": True, "sensor_connected": bool(colorsensor.connected)})
        else:
            self._send({"ok": True})

    def log_message(self, *args):  # keep the terminal quiet
        pass

server = HTTPServer(("127.0.0.1", PORT), Handler)
print(f"\nBridge running at http://127.0.0.1:{PORT}")
print("Now open lego-color-classifier.html and press 'Connect to sensor bridge'.")
print("Press Ctrl+C here to stop.\n")

try:
    server.serve_forever()
except KeyboardInterrupt:
    print("\nShutting down...")
    try:
        colorsensor.disconnect()
    except Exception:
        pass
    server.shutdown()
