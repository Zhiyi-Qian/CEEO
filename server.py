#!/usr/bin/env python3
"""
Smart Motor Lab - local server
Run:   python server.py
Open:  http://localhost:5000
"""

from flask import Flask, send_from_directory
from flask_socketio import SocketIO, emit
import legoeducation as le
import threading
import time
import queue

app = Flask(__name__, static_folder=".")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── Sensor devices (read-only, no threading issues) ───────────────────────────
sensor_devices = {
    "colorsensor": None,
    "controller":  None,
    "doublemotor": None,
}
sensor_lock    = threading.Lock()
current_sensor = "color"
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
    except Exception as e:
        print(f"  [safe error] {e}")
        return default


# ── Motor Manager ─────────────────────────────────────────────────────────────
# ALL motor I/O (read position + send commands) runs in ONE dedicated thread.
# This avoids the asyncio/BLE thread context issue where commands sent from a
# different thread than the connection thread are silently dropped.

class MotorManager:
    def __init__(self):
        self._motor   = None
        self._thread  = None
        self._target  = None   # desired angle (0-359), set from any thread
        self._stop    = False
        self.connected = False

    def connect(self, card_color, card_serial):
        """Start the motor worker thread."""
        self._stop = False
        self._target = None
        self._thread = threading.Thread(
            target=self._worker,
            args=(card_color, card_serial),
            daemon=True,
            name="motor-worker"
        )
        self._thread.start()

    def disconnect(self):
        self._stop = True
        self.connected = False
        if self._motor:
            safe(self._motor.motor_stop)
            safe(self._motor.disconnect)
        self._motor = None

    def set_target(self, angle):
        """Thread-safe: set desired motor angle (0-359)."""
        self._target = max(0, min(359, int(angle)))

    def clear_target(self):
        self._target = None
        if self._motor and self.connected:
            safe(self._motor.motor_stop)

    def _worker(self, card_color, card_serial):
        """
        Runs entirely in its own thread.
        connect() AND all subsequent motor_run / motor_stop calls happen here.
        """
        print(f"\n[motor] connecting in thread {threading.current_thread().name}...")
        try:
            motor = le.SingleMotor()
            motor.connect(card_color=card_color, card_serial=card_serial)

            if not motor.connected:
                socketio.emit("device_status", {
                    "device": "motor", "connected": False,
                    "error": "Could not connect — check card colour and serial"
                })
                return

            self._motor = motor
            self.connected = True
            socketio.emit("device_status", {"device": "motor", "connected": True})
            print("[motor] connected ✓  starting control loop")

            is_running = False   # did we issue a motor_run this iteration?

            while not self._stop and motor.connected:

                # ── Read position (same thread as connect) ──────────────────
                pos = safe(lambda: motor.motor.position % 360)
                if pos is not None:
                    socketio.emit("data", {"motor_angle": pos})

                # ── Drive toward target ─────────────────────────────────────
                target = self._target   # atomic read (GIL)
                if target is not None and pos is not None:
                    diff = target - pos
                    if diff >  180: diff -= 360
                    if diff < -180: diff += 360

                    if abs(diff) > 15:
                        spd = min(80, max(30, int(abs(diff) * 0.8)))
                        direction = (le.MOTOR_MOVE_DIRECTION_CLOCKWISE
                                     if diff > 0
                                     else le.MOTOR_MOVE_DIRECTION_COUNTERCLOCKWISE)
                        print(f"[motor] pos={pos}  target={target}  diff={diff:+.0f}  speed={spd}")
                        safe(lambda: motor.motor_run(direction=direction, speed=spd))
                        is_running = True
                    else:
                        if is_running:
                            safe(motor.motor_stop)
                            is_running = False
                            print(f"[motor] reached target {target} ✓")
                        self._target = None
                else:
                    if is_running:
                        safe(motor.motor_stop)
                        is_running = False

                time.sleep(0.15)

        except Exception as e:
            print(f"[motor] worker exception: {e}")
            socketio.emit("device_status", {
                "device": "motor", "connected": False, "error": str(e)
            })
        finally:
            self._motor = None
            self.connected = False
            print("[motor] worker thread exited")


motor_mgr = MotorManager()


# ── Sensor poll loop (read-only — no threading issues) ────────────────────────
def sensor_poll():
    while polling_active:
        with sensor_lock:
            cs  = sensor_devices["colorsensor"]
            ctl = sensor_devices["controller"]
            dm  = sensor_devices["doublemotor"]

        v = None
        if current_sensor == "color" and cs and cs.connected:
            v = safe(lambda: cs.sensor.hue)
        elif current_sensor == "controller" and ctl and ctl.connected:
            v = safe(lambda: ctl.sensor.rightPercent)
        elif current_sensor == "doublemotor" and dm and dm.connected:
            v = safe(lambda: dm.imu_device.pitch)

        if v is not None:
            socketio.emit("data", {"sensor_value": v})

        time.sleep(0.15)


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(".", "index.html")


# ── Socket.IO events ──────────────────────────────────────────────────────────
@socketio.on("connect")
def on_connect():
    emit("device_status", {"device": "motor",       "connected": motor_mgr.connected})
    with sensor_lock:
        for name, dev in sensor_devices.items():
            emit("device_status", {"device": name, "connected": bool(dev and dev.connected)})


@socketio.on("connect_device")
def on_connect_device(data):
    name       = data.get("device")
    color_str  = data.get("card_color", "AZURE").upper()
    serial     = data.get("card_serial", "0000")
    card_color = COLOR_MAP.get(color_str, le.LEGO_COLOR_AZURE)
    print(f"\n[connect] {name}  card={color_str}  serial={serial}")

    if name == "motor":
        motor_mgr.connect(card_color, serial)
        return

    def _connect_sensor():
        try:
            dev = None
            if name == "colorsensor":
                dev = le.ColorSensor()
                dev.connect(card_color=card_color, card_serial=serial)
            elif name == "controller":
                dev = le.Controller()
                dev.connect(card_color=card_color, card_serial=serial)
            elif name == "doublemotor":
                dev = le.DoubleMotor()
                dev.connect(card_color=card_color, card_serial=serial)

            if dev and dev.connected:
                with sensor_lock:
                    sensor_devices[name] = dev
                socketio.emit("device_status", {"device": name, "connected": True})
                print(f"[+] {name} connected")
            else:
                socketio.emit("device_status", {
                    "device": name, "connected": False,
                    "error": "Could not connect — check card colour and serial"
                })
        except Exception as ex:
            socketio.emit("device_status", {
                "device": name, "connected": False, "error": str(ex)
            })

    threading.Thread(target=_connect_sensor, daemon=True).start()


@socketio.on("disconnect_device")
def on_disconnect_device(data):
    name = data.get("device")
    if name == "motor":
        motor_mgr.disconnect()
        socketio.emit("device_status", {"device": "motor", "connected": False})
        return
    with sensor_lock:
        dev = sensor_devices.get(name)
        if dev:
            safe(dev.disconnect)
        sensor_devices[name] = None
    socketio.emit("device_status", {"device": name, "connected": False})
    print(f"[-] {name} disconnected")


@socketio.on("set_sensor_type")
def on_set_sensor(data):
    global current_sensor
    current_sensor = data.get("sensor", "color")
    print(f"[~] sensor type: {current_sensor}")


@socketio.on("move_motor")
def on_move_motor(data):
    angle = int(data.get("angle", 0))
    print(f"[move] target={angle}")
    motor_mgr.set_target(angle)


@socketio.on("stop_motor")
def on_stop_motor():
    motor_mgr.clear_target()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    threading.Thread(target=sensor_poll, daemon=True).start()

    print("\n" + "=" * 40)
    print("  Smart Motor Lab server")
    print("  Open http://localhost:5000")
    print("=" * 40 + "\n")

    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
