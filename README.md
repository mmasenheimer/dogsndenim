# dogsndenim

Streams a USB camera on a Raspberry Pi to a public website for a dog fashion show.

## How it works

```
camera → Pi (website.py) ──one stream──▶ Cloudflare Worker ──copies──▶ every viewer
```

- **The Pi** (`website.py`) reads the camera, encodes each frame to JPEG once, and sends it
  over a single WebSocket to the relay. Its CPU use and upload are the same for 1 viewer
  or 100.
- **The relay** (`relay/`) is a Cloudflare Worker that runs on Cloudflare's servers. It serves the
  web page (`relay/public/`) and a Durable Object copies each frame to every connected
  viewer. Viewers on slow connections skip frames instead of slowing anyone else down.
- The Pi also runs a small Flask server for checking the camera on the local network:
  `http://<pi-ip-address>:5000` shows the raw camera feed.

No Cloudflare Tunnel is needed. The site has a permanent `https://dogsndenim.<you>.workers.dev`
link that keeps working if the Pi reconnects.

## 1. Deploy the relay (once, from your computer)

You need a free Cloudflare account and Node.js.

```bash
cd relay
npm install
npx wrangler login                    # opens the browser to sign in to Cloudflare
npx wrangler deploy                   # prints your https://dogsndenim.<you>.workers.dev link
npx wrangler secret put INGEST_KEY    # paste a long random password, e.g. from `openssl rand -hex 32`
```

`INGEST_KEY` is the password the Pi uses to publish video. Without it anyone could send
their own video to your viewers. Run `npx wrangler deploy` again after changing anything
in `relay/`.

## 2. Set up the Pi

```bash
cd ~/dogsndenim
python3 -m venv venv
venv/bin/pip install -r requirements.txt
ls /dev/video*                        # the camera should show up as /dev/video0
```

Create `~/dogsndenim/relay.env` with the relay address and the same key (this file is
ignored by git):

```
RELAY_URL=wss://dogsndenim.<you>.workers.dev/ingest
RELAY_KEY=<the INGEST_KEY you set above>
```

Install the service so it starts on boot (see the comments at the top of
`dogsndenim.service`; change `User` and the paths if your username isn't `pi`):

```bash
sudo cp dogsndenim.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dogsndenim
journalctl -u dogsndenim -f           # should print "Connected to the relay"
```

To run it by hand instead: `set -a; . ./relay.env; set +a; venv/bin/python website.py`

## Settings

Set these in `dogsndenim.service` (or as environment variables):

| Variable        | Default | Meaning                                    |
|-----------------|---------|--------------------------------------------|
| `CAMERA_INDEX`  | `0`     | Which camera to use (`/dev/videoN`)        |
| `CAMERA_WIDTH`  | `1280`  | Requested frame width                      |
| `CAMERA_HEIGHT` | `720`   | Requested frame height                     |
| `CAMERA_FPS`    | `15`    | Maximum frames per second sent             |
| `JPEG_QUALITY`  | `80`    | JPEG quality, 1–100                        |
| `PORT`          | `5000`  | Port for the local camera check            |
| `RELAY_URL`     | none    | Relay address; nothing is published if unset |
| `RELAY_KEY`     | none    | Must match the relay's `INGEST_KEY`        |

## Upload speed and data

The Pi uploads one stream no matter how many people watch, so the only question is whether
the connection can carry that one stream. Measure it on the Pi with the camera pointed at a
typical scene:

```bash
FPS=15   # your CAMERA_FPS
BYTES=$(curl -s localhost:5000/snapshot | wc -c)
echo "Upload needed: $(echo "$BYTES*8*$FPS/1000000" | bc -l | cut -c1-5) Mbps"
```

Rough figures (the real numbers depend on what the camera sees):

| Settings (width × height, fps, quality) | Upload | Data for 2 hours |
|---|---|---|
| 1280×720, 15 fps, q80 (default) | ~10–15 Mbps | ~9–13 GB |
| 640×480, 10 fps, q70 | ~2–3 Mbps | ~2 GB |
| 640×360, 8 fps, q60 | ~1 Mbps | ~1 GB |

Keep the upload well under what the connection can do. The Pi 3B's Wi-Fi is 2.4 GHz only,
and a phone hotspot at a crowded event may only manage 5–20 Mbps up, so 640×480 or lower is
the safe choice. If the upload can't keep up, viewers see a lower frame rate rather than a
frozen stream.

## Cloudflare free plan

The Worker and Durable Object fit the free plan for an event of a few hours. Each frame the
Pi sends and each viewer's frame acknowledgement (one per 5 frames) count toward the daily
Durable Object request limit, at a reduced rate for WebSocket messages. Lower FPS and a
modest audience use less. Check usage in the Cloudflare dashboard during the dry run.

## On the day

1. Power up the Pi and camera; check `journalctl -u dogsndenim -f` says "Connected to the relay".
2. Open the workers.dev link on a phone over cellular data to confirm it's live.
3. Share the link. Anyone with it can watch, so make sure the camera can't see anything private.

Do a full dry run a day or two before, at the venue if possible, with a few friends watching.

## Working on the page

`relay/public/` is plain HTML and CSS, so it can be opened with VS Code's Live Server for
layout (no video). To test everything locally:

```bash
cd relay
echo "INGEST_KEY=test" > .dev.vars
npx wrangler dev                      # http://localhost:8787
# in another terminal, from the project folder:
RELAY_URL=ws://localhost:8787/ingest RELAY_KEY=test PORT=5001 venv/bin/python website.py
```
