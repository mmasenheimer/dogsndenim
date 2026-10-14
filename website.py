import cv2
from flask import Flask, render_template, Response

app = Flask(__name__)

# Initialize the camera. '0' is usually the default built-in or USB camera.
# If you have multiple cameras, you might need to change this to 1 or 2.
camera = cv2.VideoCapture(0)

def generate_frames():
    while True:
        # Capture frame-by-frame from the camera
        success, frame = camera.read()
        if not success:
            break
        else:
            # Encode the frame in JPEG format
            ret, buffer = cv2.imencode('.jpg', frame)
            frame = buffer.tobytes()

            # Use yielding to stream the video chunks back to the browser
            # multipart/x-mixed-replace tells the browser to constantly replace the old image with the new one
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/')
def index():
    # Renders the main webpage (templates/index.html)
    return render_template('index.html')

@app.route('/video_feed')
def video_feed():
    # Returns the live streaming response
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    # host='0.0.0.0' allows other devices on your local network to view the stream
    app.run(host='0.0.0.0', port=5000, threaded=True)
