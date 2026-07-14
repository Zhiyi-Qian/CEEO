#!/usr/bin/env python3
"""
Smart Motor Lab — server
API ref: https://github.com/LEGO/LEGOEducation
Run:   python smart_motor_server.py
Open:  http://localhost:5000
"""

from flask import Flask, send_from_directory
from flask_socketio import SocketIO, emit
import legoeducation as le
import legoeducation.basic_device as _bd
import threading
import time

app = Flask(__name__, static_folder=".")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── Firmware version compatibility patch ──────────────────────────────────────
# The library enforces exact RPC build-number matching (e.g. 1.0.73 vs 1.0.67).
# Devices with older firmware are refused even when the protocol is compatible.
# This patch makes the check accept any device with the same major.minor version
# (1.0.x), so you can use the controller without a firmware update.

class _FlexRPC(tuple):
    """RPC version that accepts any device sharing the same major.minor version."""
    def __eq__(self, other):
        if isinstance(other, tuple) and len(other) == 3:
            return self[0] == other[0] and self[1] == other[1]
        return tuple.__eq__(self, other)
    def __ne__(self, other):   return not self.__eq__(other)
    def __lt__(self, other):
        if isinstance(other, tuple) and len(other) == 3:
            if self[0] == other[0] and self[1] == other[1]: return False
        return tuple.__lt__(self, other)
    def __gt__(self, other):
        if isinstance(other, tuple) and len(other) == 3:
            if self[0] == other[0] and self[1] == other[1]: return False
        return tuple.__gt__(self, other)

_original_rpc = _bd.RPC_VERSION
_bd.RPC_VERSION = _FlexRPC(_original_rpc)
print(f"[patch] firmware check patched — accepts any 1.0.x device "
      f"(package expects {_original_rpc})")

COLOR_MAP = {
    "AZURE":  le.LEGO_COLOR_AZURE,
    "RED":    le.LEGO_COLOR_RED,
    "PURPLE": le.LEGO_COLOR_PURPLE,
    "BLUE":   le.LEGO_COLOR_BLUE,
    "GREEN":  le.LEGO_COLOR_GREEN,
    "YELLOW": le.LEGO_COLOR_YELLOW,
    "ORANGE": le.LEGO_COLOR_ORANGE,
    "WHITE":  le.LEGO_COLOR_WHITE,
}

current_mode = "train"


def safe(fn, default=None):
    try:
        return fn()
    except Exception as e:
        print(f"  [err] {e}")
        return default


def connect_device(dev, card_color, card_serial):
    """
    Connect a legoeducation device, handling firmware warnings gracefully.
    Returns (connected: bool, error_msg: str|None).
    """
    error_msg = None
    try:
        dev.connect(card_color=card_color, card_serial=card_serial)
    except Exception as e:
        error_msg = str(e)
        msg = error_msg.lower()
        if "firmware" in msg or "update" in msg:
            print(f"  [firmware] {e}  — attempting to use device anyway…")
        else:
            print(f"  [connect error] {e}")
    connected = getattr(dev, "connected", False)
    return connected, error_msg


# ── Single Motor Manager ──────────────────────────────────────────────────────
# Position mode: motor_run_for_degrees fires ONCE when target changes.
#   No feedback loop → no oscillation, no electromagnetic-braking jerk.
# Speed mode: motor_run continuous at commanded speed.

class SingleMotorManager:
    def __init__(self):
        self._motor    = None
        self._thread   = None
        self._stop     = False
        self._sub_mode = 'position'
        self._target   = None   # angle 0-359 (position mode)
        self._speed    = 0      # -100 to 100 (speed mode)
        self.connected = False

    def connect(self, card_color, card_serial):
        self._stop   = False
        self._target = None
        self._speed  = 0
        self._thread = threading.Thread(
            target=self._worker, args=(card_color, card_serial),
            daemon=True, name="single-motor")
        self._thread.start()

    def set_sub_mode(self, mode):
        self._sub_mode = mode
        self._target = None
        self._speed  = 0

    def set_target(self, angle):
        self._target = max(0, min(359, int(angle)))

    def set_speed(self, speed):
        self._speed = max(-100, min(100, int(speed)))

    def clear(self):
        self._target = None
        self._speed  = 0

    def disconnect(self):
        self._stop = True
        self.connected = False
        if self._motor:
            safe(self._motor.motor_stop)
            safe(self._motor.disconnect)
        self._motor = None

    def _worker(self, card_color, card_serial):
        print(f"\n[single motor] connecting…")
        try:
            m = le.SingleMotor()
            ok, err = connect_device(m, card_color, card_serial)
            if not ok:
                socketio.emit("device_status", {
                    "device": "motor", "connected": False,
                    "error": err or "Could not connect — check card colour, serial, and firmware"})
                return

            self._motor    = m
            self.connected = True
            socketio.emit("device_status", {"device": "motor", "connected": True})
            print("[single motor] connected ✓")

            last_exec_target = None  # last target we actually commanded
            speed_running    = False

            while not self._stop and m.connected:
                # Read position — same thread as connect() (critical for BLE)
                pos = safe(lambda: m.motor.position % 360)
                if pos is not None:
                    socketio.emit("data", {"motor_angle": pos})

                if self._sub_mode == 'position':
                    # ── Position mode ─────────────────────────────────────
                    # KEY: fire motor_run_for_degrees ONCE when target changes.
                    # The motor moves exactly diff degrees and stops itself —
                    # no feedback loop, no electromagnetic-braking jerk.
                    target = self._target
                    if target is not None and target != last_exec_target and pos is not None:
                        diff = target - pos
                        if diff >  180: diff -= 360
                        if diff < -180: diff += 360
                        if abs(diff) > 3:
                            print(f"[single motor] {pos}° → {target}° (diff {diff:+.0f}°)")
                            safe(lambda d=int(diff): m.motor_run_for_degrees(d))
                        last_exec_target = target

                else:
                    # ── Speed mode ────────────────────────────────────────
                    spd = self._speed
                    if abs(spd) > 5:
                        if spd > 0:
                            safe(lambda s=spd: m.motor_run(
                                direction=le.MOTOR_MOVE_DIRECTION_CLOCKWISE, speed=s))
                        else:
                            safe(lambda s=abs(spd): m.motor_run(
                                direction=le.MOTOR_MOVE_DIRECTION_COUNTERCLOCKWISE, speed=s))
                        speed_running = True
                    else:
                        if speed_running:
                            safe(m.motor_stop)
                            speed_running = False

                time.sleep(0.1)

        except Exception as e:
            print(f"[single motor] worker error: {e}")
            socketio.emit("device_status", {
                "device": "motor", "connected": False, "error": str(e)})
        finally:
            self._motor    = None
            self.connected = False
            print("[single motor] thread exited")


# ── Double Motor Manager ──────────────────────────────────────────────────────
# Position mode: motor_run_for_degrees fires ONCE per target change per motor.
# Speed mode:    motor_run continuous, only re-issued when speed changes.

class DoubleMotorManager:
    def __init__(self):
        self._motor      = None
        self._thread     = None
        self._stop       = False
        self._sub_mode   = 'speed'
        self._left       = 0
        self._right      = 0
        self._target_set = False
        self.connected   = False

    def connect(self, card_color, card_serial):
        self._stop       = False
        self._left       = 0
        self._right      = 0
        self._target_set = False
        self._thread = threading.Thread(
            target=self._worker, args=(card_color, card_serial),
            daemon=True, name="double-motor")
        self._thread.start()

    def set_sub_mode(self, mode):
        self._sub_mode   = mode
        self._left       = 0
        self._right      = 0
        self._target_set = False

    def set_speeds(self, left, right):
        self._left  = max(-100, min(100, int(left)))
        self._right = max(-100, min(100, int(right)))

    def set_targets(self, left, right):
        self._left       = max(0, min(359, int(left)))
        self._right      = max(0, min(359, int(right)))
        self._target_set = True

    def stop_all(self):
        self._left  = 0
        self._right = 0

    def disconnect(self):
        self._stop     = True
        self.connected = False
        if self._motor:
            safe(self._motor.motor_stop)
            safe(self._motor.disconnect)
        self._motor = None

    def _apply_speeds(self, dm, left, right):
        """Apply left/right speeds using the documented independent motor API."""
        db = 5
        lo = abs(left)  > db
        ro = abs(right) > db
        if not lo and not ro:
            safe(dm.motor_stop)
            return
        if lo and ro:
            dl = le.MOTOR_MOVE_DIRECTION_CLOCKWISE if left  > 0 else le.MOTOR_MOVE_DIRECTION_COUNTERCLOCKWISE
            dr = le.MOTOR_MOVE_DIRECTION_CLOCKWISE if right > 0 else le.MOTOR_MOVE_DIRECTION_COUNTERCLOCKWISE
            sl, sr = abs(left), abs(right)
            safe(lambda: dm.motor_run(direction=dl, motor=le.MOTOR_LEFT,  speed=sl))
            safe(lambda: dm.motor_run(direction=dr, motor=le.MOTOR_RIGHT, speed=sr))
        elif lo:
            dl = le.MOTOR_MOVE_DIRECTION_CLOCKWISE if left > 0 else le.MOTOR_MOVE_DIRECTION_COUNTERCLOCKWISE
            sl = abs(left)
            safe(dm.motor_stop)
            safe(lambda: dm.motor_run(direction=dl, motor=le.MOTOR_LEFT, speed=sl))
        else:
            dr = le.MOTOR_MOVE_DIRECTION_CLOCKWISE if right > 0 else le.MOTOR_MOVE_DIRECTION_COUNTERCLOCKWISE
            sr = abs(right)
            safe(dm.motor_stop)
            safe(lambda: dm.motor_run(direction=dr, motor=le.MOTOR_RIGHT, speed=sr))

    def _worker(self, card_color, card_serial):
        print(f"\n[double motor] connecting…")
        try:
            dm = le.DoubleMotor()
            ok, err = connect_device(dm, card_color, card_serial)
            if not ok:
                socketio.emit("device_status", {
                    "device": "doublemotor", "connected": False,
                    "error": err or "Could not connect — check card colour, serial, and firmware"})
                return

            self._motor    = dm
            self.connected = True
            socketio.emit("device_status", {"device": "doublemotor", "connected": True})
            print("[double motor] connected ✓")

            prev_left       = None
            prev_right      = None
            last_exec_left  = None   # last targets we fired motor_run_for_degrees for
            last_exec_right = None

            while not self._stop and dm.connected:
                # IMU pitch for tilt-as-sensor
                pitch = safe(lambda: dm.imu_device.pitch)
                if pitch is not None:
                    socketio.emit("data", {"double_motor_pitch": pitch})

                left  = self._left
                right = self._right

                if self._sub_mode == 'speed':
                    # ── Speed mode ────────────────────────────────────────
                    # Only re-issue command when speed values change
                    if left != prev_left or right != prev_right:
                        self._apply_speeds(dm, left, right)
                        prev_left, prev_right = left, right

                else:
                    # ── Position mode ─────────────────────────────────────
                    # Read positions for display
                    lpos = safe(lambda: dm.motor[le.MOTOR_LEFT].position  % 360)
                    rpos = safe(lambda: dm.motor[le.MOTOR_RIGHT].position % 360)
                    if lpos is not None:
                        socketio.emit("data", {"double_motor_positions":
                                               {"left": lpos, "right": rpos or 0}})

                    # Fire motor_run_for_degrees ONCE when target changes.
                    # No feedback loop → no nonstop rotation.
                    if self._target_set and (left != last_exec_left or right != last_exec_right):
                        if lpos is not None and rpos is not None:
                            ldiff = left  - lpos
                            rdiff = right - rpos
                            if ldiff >  180: ldiff -= 360
                            if ldiff < -180: ldiff += 360
                            if rdiff >  180: rdiff -= 360
                            if rdiff < -180: rdiff += 360

                            print(f"[double motor] L→{left}° (Δ{ldiff:+.0f}) R→{right}° (Δ{rdiff:+.0f})")
                            if abs(ldiff) > 3:
                                safe(lambda d=int(ldiff): dm.motor_run_for_degrees(
                                    d, motor=le.MOTOR_LEFT))
                            if abs(rdiff) > 3:
                                safe(lambda d=int(rdiff): dm.motor_run_for_degrees(
                                    d, motor=le.MOTOR_RIGHT))

                        last_exec_left  = left
                        last_exec_right = right

                    prev_left, prev_right = left, right

                time.sleep(0.15)

        except Exception as e:
            print(f"[double motor] worker error: {e}")
            socketio.emit("device_status", {
                "device": "doublemotor", "connected": False, "error": str(e)})
        finally:
            self._motor    = None
            self.connected = False
            print("[double motor] thread exited")


# ── Sensor-only devices ───────────────────────────────────────────────────────

sensor_devices = {"colorsensor": None, "controller": None}
sensor_lock     = threading.Lock()
current_sensor  = "color"
polling_active  = True

single_mgr = SingleMotorManager()
double_mgr = DoubleMotorManager()


def sensor_poll():
    while polling_active:
        with sensor_lock:
            cs  = sensor_devices["colorsensor"]
            ctl = sensor_devices["controller"]
        v = None
        if current_sensor == "color" and cs and cs.connected:
            v = safe(lambda: cs.sensor.hue)
        elif current_sensor == "controller" and ctl and ctl.connected:
            v = safe(lambda: ctl.sensor.rightPercent)
        if v is not None:
            socketio.emit("data", {"sensor_value": v})
        time.sleep(0.15)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "smart_motor_lab.html")


# ── Socket.IO events ──────────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    emit("device_status", {"device": "motor",       "connected": single_mgr.connected})
    emit("device_status", {"device": "doublemotor", "connected": double_mgr.connected})
    with sensor_lock:
        for name, dev in sensor_devices.items():
            emit("device_status", {"device": name, "connected": bool(dev and dev.connected)})


@socketio.on("set_mode")
def on_set_mode(data):
    global current_mode
    current_mode = data.get("mode", "train")


@socketio.on("set_output_sub_mode")
def on_set_output_sub_mode(data):
    mode = data.get("sub_mode", "position")
    single_mgr.set_sub_mode(mode)
    double_mgr.set_sub_mode(mode)
    print(f"[sub_mode] → {mode}")


@socketio.on("connect_device")
def on_connect_device(data):
    name       = data.get("device")
    color_str  = data.get("card_color", "AZURE").upper()
    serial     = data.get("card_serial", "0000")
    card_color = COLOR_MAP.get(color_str, le.LEGO_COLOR_AZURE)
    print(f"\n[connect] {name}  card={color_str}  serial={serial}")

    if name == "motor":
        single_mgr.connect(card_color, serial)
        return
    if name == "doublemotor":
        double_mgr.connect(card_color, serial)
        return

    def _connect_sensor():
        try:
            dev = None
            if name == "colorsensor":
                dev = le.ColorSensor()
            elif name == "controller":
                dev = le.Controller()
            else:
                return

            ok, err = connect_device(dev, card_color, serial)
            if ok:
                with sensor_lock:
                    sensor_devices[name] = dev
                socketio.emit("device_status", {"device": name, "connected": True})
                print(f"[+] {name} connected")
            else:
                socketio.emit("device_status", {
                    "device": name, "connected": False,
                    "error": err or "Could not connect — check card colour, serial, and firmware"})
        except Exception as ex:
            socketio.emit("device_status", {
                "device": name, "connected": False, "error": str(ex)})

    threading.Thread(target=_connect_sensor, daemon=True).start()


@socketio.on("disconnect_device")
def on_disconnect_device(data):
    name = data.get("device")
    if name == "motor":
        single_mgr.disconnect()
        socketio.emit("device_status", {"device": "motor", "connected": False})
    elif name == "doublemotor":
        double_mgr.disconnect()
        socketio.emit("device_status", {"device": "doublemotor", "connected": False})
    else:
        with sensor_lock:
            dev = sensor_devices.get(name)
            if dev: safe(dev.disconnect)
            sensor_devices[name] = None
        socketio.emit("device_status", {"device": name, "connected": False})
    print(f"[-] {name} disconnected")


@socketio.on("set_sensor_type")
def on_set_sensor(data):
    global current_sensor
    current_sensor = data.get("sensor", "color")


# ── Motor control ─────────────────────────────────────────────────────────────

@socketio.on("move_motor")
def on_move_motor(data):
    single_mgr.set_target(int(data.get("angle", 0)))

@socketio.on("set_motor_speed")
def on_set_motor_speed(data):
    single_mgr.set_speed(int(data.get("speed", 0)))

@socketio.on("stop_motor")
def on_stop_motor():
    single_mgr.clear()

@socketio.on("move_double_motor")
def on_move_double_motor(data):
    double_mgr.set_speeds(int(data.get("left", 0)), int(data.get("right", 0)))

@socketio.on("move_double_motor_position")
def on_move_double_motor_position(data):
    double_mgr.set_targets(int(data.get("left", 0)), int(data.get("right", 0)))

@socketio.on("stop_double_motor")
def on_stop_double_motor():
    double_mgr.stop_all()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    threading.Thread(target=sensor_poll, daemon=True).start()
    print("\n" + "=" * 42)
    print("  Smart Motor Lab server")
    print("  Open http://localhost:5000")
    print("=" * 42 + "\n")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)