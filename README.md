# dogsndenim

A small Flask website that streams a USB camera from a Raspberry Pi.

## Setup on the Pi

```bash
cd ~/dogsndenim
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

Check that the camera is detected (it should appear as `/dev/video0`):

```bash
ls /dev/video*
```

Run it:

```bash
venv/bin/python website.py
```

Then open `http://<pi-ip-address>:5000` from any device on the same network.
`hostname -I` on the Pi prints its IP address.

## Start on boot

See the comments at the top of `dogsndenim.service`. Change `User` and the paths in it if
your username isn't `pi` or the project lives somewhere else.

## Settings

Set these as environment variables (or edit them in `dogsndenim.service`):

| Variable        | Default | Meaning                                  |
|-----------------|---------|------------------------------------------|
| `CAMERA_INDEX`  | `0`     | Which camera to use (`/dev/videoN`)      |
| `CAMERA_WIDTH`  | `1280`  | Requested frame width                    |
| `CAMERA_HEIGHT` | `720`   | Requested frame height                   |
| `CAMERA_FPS`    | `15`    | Maximum frames per second sent to viewers |
| `JPEG_QUALITY`  | `80`    | JPEG quality, 1–100                      |
| `PORT`          | `5000`  | Port the website listens on              |

If the stream is choppy or the Pi runs hot, lower the resolution, FPS or JPEG quality.

## Routes

- `/`: the web page
- `/video_feed`: the MJPEG stream
- `/snapshot`: the current frame as a JPEG
- `/status`: JSON with camera connection state and measured FPS

The site has no login. Keep it on your home network, and don't port-forward it to the internet.
