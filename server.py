#!/usr/bin/env python3
"""
Smart Motor Lab - local server
Bridges legoeducation LEGO CS&AI devices to the web frontend via Socket.IO.

Install:  pip install flask flask-socketio legoeducation
Run:      python server.py
Open:     http://localhost:5000
"""

from flask import Flask, send_from_directory
from flask_socketio import SocketIO, emit
import legoeducation as le
import threading
import time
import os

app = Flask(__name__, static_folder=".")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── Device state ──────────────────────────────────────────────────────────────

devices = {
    "motor":       None,
    "colorsensor": None,
    "controller":  None,
    "doublemotor": None,
}
device_lock    = threading.Lock()
current_sensor = "color"   # tracks which sensor the UI has selected
polling_active = True

COLOR_MAP = {
    "AZURE":  le.LEGO_COLOR_AZURE,
    "RED":    le.LEGO_COLOR_RED,
    "PURPLE": le.LEGO_COLOR_PURPLE,
    "BLUE":   le.LEGO_COLOR_BLUE,
    "GREEN":  le.LEGO_COLOR_GREEN,
    "YELLOW": le.LEGO_COLOR_YELLOW,
    "WHITE":  le.LEGO_COLOR_WHITE,
}


def safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


# ── Background polling loop (20 Hz) ──────────────────────────────────────────

def poll():
    while polling_active:
        with device_lock:
            motor       = devices["motor"]
            colorsensor = devices["colorsensor"]
            controller  = devices["controller"]
            doublemotor = devices["doublemotor"]

        payload = {}

        if motor and motor.connected:
            angle = safe(lambda: motor.motor.position % 360)
            if angle is not None:
                payload["motor_angle"] = angle

        if current_sensor == "color" and colorsensor and colorsensor.connected:
            v = safe(lambda: colorsensor.sensor.hue)
            if v is not None:
                payload["sensor_value"] = v

        elif current_sensor == "controller" and controller and controller.connected:
            v = safe(lambda: controller.sensor.rightPercent)
            if v is not None:
                payload["sensor_value"] = v

        elif current_sensor == "doublemotor" and doublemotor and doublemotor.connected:
            v = safe(lambda: doublemotor.imu_device.pitch)
            if v is not None:
                payload["sensor_value"] = v

        if payload:
            socketio.emit("data", payload)

        time.sleep(0.05)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "index.html")


# ── Socket.IO events ──────────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    """Send current device statuses to newly connected browser."""
    with device_lock:
        for name, dev in devices.items():
            emit("device_status", {
                "device":    name,
                "connected": bool(dev and dev.connected),
            })


@socketio.on("connect_device")
def on_connect_device(data):
    name       = data.get("device")
    color_str  = data.get("card_color", "AZURE").upper()
    serial     = data.get("card_serial", "0000")
    card_color = COLOR_MAP.get(color_str, le.LEGO_COLOR_AZURE)

    def _connect():
        try:
            with device_lock:
                old = devices.get(name)
                if old and old.connected:
                    safe(old.disconnect)

            dev = None
            if name == "motor":
                dev = le.SingleMotor()
                dev.connect(card_color=card_color, card_serial=serial)
            elif name == "colorsensor":
                dev = le.ColorSensor()
                dev.connect(card_color=card_color, card_serial=serial)
            elif name == "controller":
                dev = le.Controller()
                dev.connect(card_color=card_color, card_serial=serial)
            elif name == "doublemotor":
                dev = le.DoubleMotor()
                dev.connect(card_color=card_color, card_serial=serial)

            if dev and dev.connected:
                with device_lock:
                    devices[name] = dev
                socketio.emit("device_status", {"device": name, "connected": True})
                print(f"[+] {name} connected (card {color_str} / {serial})")
            else:
                socketio.emit("device_status", {
                    "device": name, "connected": False,
                    "error": "Could not connect — check card color and serial number",
                })
        except Exception as ex:
            socketio.emit("device_status", {
                "device": name, "connected": False, "error": str(ex),
            })

    threading.Thread(target=_connect, daemon=True).start()


@socketio.on("disconnect_device")
def on_disconnect_device(data):
    name = data.get("device")
    with device_lock:
        dev = devices.get(name)
        if dev:
            safe(dev.disconnect)
        devices[name] = None
    socketio.emit("device_status", {"device": name, "connected": False})
    print(f"[-] {name} disconnected")


@socketio.on("set_sensor_type")
def on_set_sensor(data):
    global current_sensor
    current_sensor = data.get("sensor", "color")


@socketio.on("move_motor")
def on_move_motor(data):
    target = int(data.get("angle", 0))
    with device_lock:
        motor = devices.get("motor")
    if not (motor and motor.connected):
        return
    def _move():
        current = safe(lambda: motor.motor.position % 360, 0)
        diff = target - current
        if abs(diff) > 5:
            safe(lambda: motor.motor_run_for_degrees(diff))
    threading.Thread(target=_move, daemon=True).start()


@socketio.on("stop_motor")
def on_stop_motor():
    with device_lock:
        motor = devices.get("motor")
    if motor and motor.connected:
        safe(motor.motor_stop)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    poll_thread = threading.Thread(target=poll, daemon=True)
    poll_thread.start()

    print("\n" + "="*40)
    print("  Smart Motor Lab server")
    print("  Open http://localhost:5000")
    print("="*40 + "\n")

    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
