import os
import sys
import threading
import time

import cv2
import numpy as np
import websocket
from flask import Flask, Response, jsonify, redirect

# Settings can be overridden with environment variables (see dogsndenim.service).
CAMERA_INDEX = int(os.environ.get("CAMERA_INDEX", 0))
CAMERA_WIDTH = int(os.environ.get("CAMERA_WIDTH", 1280))
CAMERA_HEIGHT = int(os.environ.get("CAMERA_HEIGHT", 720))
CAMERA_FPS = float(os.environ.get("CAMERA_FPS", 15))
JPEG_QUALITY = int(os.environ.get("JPEG_QUALITY", 80))
RETRY_SECONDS = 2.0
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", 5000))
# The Cloudflare relay that viewers watch through, e.g. wss://dogsndenim.<you>.workers.dev/ingest
RELAY_URL = os.environ.get("RELAY_URL")
RELAY_KEY = os.environ.get("RELAY_KEY", "")


def make_placeholder(text):
    # A plain gray frame shown to viewers while the camera is unavailable
    image = np.full((CAMERA_HEIGHT, CAMERA_WIDTH, 3), 40, dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    (w, h), _ = cv2.getTextSize(text, font, 1.2, 2)
    origin = ((CAMERA_WIDTH - w) // 2, (CAMERA_HEIGHT + h) // 2)
    cv2.putText(image, text, origin, font, 1.2, (200, 200, 200), 2, cv2.LINE_AA)
    return cv2.imencode(".jpg", image)[1].tobytes()


class Camera:
    """Reads the camera on one background thread and shares the latest frame.

    Every viewer gets the same JPEG, so adding viewers doesn't add camera reads
    or encoding work, and viewers don't steal frames from each other.
    """

    def __init__(self):
        self._condition = threading.Condition()
        self._frame = make_placeholder("Starting camera...")
        self._frame_id = 0
        self.connected = False
        self.measured_fps = 0.0
        self._last_frame_time = None
        threading.Thread(target=self._run, daemon=True).start()

    def _publish(self, jpeg):
        with self._condition:
            self._frame = jpeg
            self._frame_id += 1
            self._condition.notify_all()

    def _open(self):
        # V4L2 is the native video API on the Pi; elsewhere let OpenCV choose
        backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
        capture = cv2.VideoCapture(CAMERA_INDEX, backend)
        if not capture.isOpened():
            capture.release()
            return None
        # Many USB cameras only reach 720p+ at a usable frame rate in MJPG mode
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
        capture.set(cv2.CAP_PROP_FPS, CAMERA_FPS)
        # Keep the driver buffer small so viewers see the newest frame, not a stale one
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return capture

    def _run(self):
        interval = 1.0 / CAMERA_FPS
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]

        while True:
            capture = self._open()
            if capture is None:
                print(f"Camera {CAMERA_INDEX} not found, retrying...", flush=True)
                self._publish(make_placeholder("Camera unavailable"))
                time.sleep(RETRY_SECONDS)
                continue

            print(f"Camera {CAMERA_INDEX} opened", flush=True)
            self.connected = True
            failures = 0
            next_send = 0.0

            while failures < 10:
                success, frame = capture.read()
                if not success:
                    failures += 1
                    time.sleep(0.1)
                    continue
                failures = 0

                # Drop frames beyond the FPS cap to save CPU and bandwidth. Frames are
                # sent on a fixed schedule, with half an interval of slack, because a
                # camera running at exactly CAMERA_FPS delivers some frames slightly
                # early and a strict gap check would drop about half of them.
                now = time.monotonic()
                if now < next_send - interval / 2:
                    continue
                # Move to the next slot, or restart the schedule after a stall
                next_send = max(next_send + interval, now)
                if self._last_frame_time is not None:
                    instant = 1.0 / (now - self._last_frame_time)
                    self.measured_fps = 0.9 * self.measured_fps + 0.1 * instant
                self._last_frame_time = now

                ok, buffer = cv2.imencode(".jpg", frame, encode_params)
                if ok:
                    self._publish(buffer.tobytes())

            # Too many failed reads in a row: the camera was probably unplugged
            print(f"Camera {CAMERA_INDEX} stopped responding, reconnecting...", flush=True)
            capture.release()
            self.connected = False
            self.measured_fps = 0.0
            self._last_frame_time = None
            self._publish(make_placeholder("Camera disconnected - reconnecting..."))
            time.sleep(RETRY_SECONDS)

    def latest(self):
        with self._condition:
            return self._frame

    def frames(self):
        last_id = -1
        while True:
            with self._condition:
                # Wait for a new frame; on timeout, resend the last one as a keepalive
                self._condition.wait_for(lambda: self._frame_id != last_id, timeout=5)
                frame, last_id = self._frame, self._frame_id
            yield frame

    def stream(self):
        for frame in self.frames():
            # multipart/x-mixed-replace tells the browser to keep replacing the old image with the new one
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")


def push_to_relay(camera):
    """Sends each frame once to the Cloudflare relay, which copies it to every viewer.

    The Pi's work and upload stay the same no matter how many people are watching.
    """
    while True:
        try:
            relay = websocket.create_connection(
                RELAY_URL, timeout=10, header=[f"Authorization: Bearer {RELAY_KEY}"])
        except Exception as error:
            print(f"Can't reach the relay ({error}), retrying...", flush=True)
            time.sleep(RETRY_SECONDS)
            continue

        print("Connected to the relay", flush=True)
        try:
            # If the upload can't keep up, frames() skips to the newest frame
            for frame in camera.frames():
                relay.send_binary(frame)
        except Exception as error:
            print(f"Lost the relay connection ({error}), reconnecting...", flush=True)
        finally:
            relay.close()
        time.sleep(RETRY_SECONDS)


# Flask is only for checking the camera on the local network; the public site is the relay
app = Flask(__name__)
camera = Camera()
if RELAY_URL:
    threading.Thread(target=push_to_relay, args=(camera,), daemon=True).start()
else:
    print("RELAY_URL isn't set, so nothing is sent to Cloudflare", flush=True)


@app.route("/")
def index():
    return redirect("/video_feed")


@app.route("/video_feed")
def video_feed():
    return Response(camera.stream(), mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-cache"})


@app.route("/snapshot")
def snapshot():
    return Response(camera.latest(), mimetype="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.route("/status")
def status():
    return jsonify(connected=camera.connected, fps=round(camera.measured_fps, 1),
                   width=CAMERA_WIDTH, height=CAMERA_HEIGHT)


if __name__ == "__main__":
    # host='0.0.0.0' allows other devices on your local network to view the stream
    app.run(host=HOST, port=PORT, threaded=True)
