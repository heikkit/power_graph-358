from flask import Flask, request, render_template_string, send_file
from datetime import datetime, timedelta, timezone
import json
import os
import matplotlib.pyplot as plt
import io

app = Flask(__name__)

DATA_FILE = 'power_data.json'
TIMEZONE = timezone.utc

HTML_TEMPLATE = '''
<!doctype html>
<html>
<head>
    <title>Power Status</title>
    <meta http-equiv="refresh" content="60">
</head>
<body>
    <h1>Power is: {{ status }}</h1>
    <img src="/graph.png" alt="Graph">
</body>
</html>
'''


def get_rounded_time(dt=None):
    dt = dt or datetime.now(TIMEZONE)
    return dt.replace(second=0, microsecond=0, minute=dt.minute // 5 * 5)


def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, 'r') as f:
        raw = json.load(f)
        return {datetime.fromisoformat(k): v for k, v in raw.items()}


def save_data(data):
    with open(DATA_FILE, 'w') as f:
        serializable = {k.isoformat(): v for k, v in sorted(data.items())}
        json.dump(serializable, f, indent=2)


@app.route('/post', methods=['POST'])
def receive_post():
    now = datetime.now(TIMEZONE)
    current_time = get_rounded_time(now)

    data = load_data()
    if current_time not in data:
        data[current_time] = 1
        print(f"[POST] Logged 1 at {current_time.isoformat()}")
    else:
        print(f"[POST] Entry already exists at {current_time.isoformat()}, not overwriting.")

    if data:
        previous_time = current_time - timedelta(minutes=5)
        backfilled = 0
        while previous_time not in data:
            data[previous_time] = 0
            print(f"[BACKFILL] Added 0 at {previous_time.isoformat()}")
            backfilled += 1
            previous_time -= timedelta(minutes=5)
            if previous_time < min(data.keys()):
                break
        if backfilled:
            print(f"[BACKFILL] {backfilled} missing interval(s).")
    else:
        print("[BACKFILL] Skipped: no previous data exists.")

    save_data(data)
    return 'OK'


@app.route('/status_text')
def status_text():
    data = load_data()
    if not data:
        return render_template_string(HTML_TEMPLATE, status="UNKNOWN")

    now = datetime.now(TIMEZONE)
    latest_time = max(data.keys())
    latest_value = data[latest_time]

    expected_time = get_rounded_time(now) - timedelta(minutes=5)
    grace_deadline = expected_time + timedelta(minutes=1)

    if latest_time >= expected_time and now <= grace_deadline:
        status = "ON" if latest_value == 1 else "OFF"
    else:
        status = "OFF"

    return render_template_string(HTML_TEMPLATE, status=status)


@app.route('/graph.png')
def graph():
    data = load_data()
    if not data:
        return "No data available", 404

    times = sorted(data.keys())
    values = [data[t] for t in times]

    plt.figure(figsize=(10, 3))
    plt.plot(times, values, drawstyle='steps-post', marker='o')
    plt.title("Power Status Over Time")
    plt.xlabel("Time")
    plt.ylabel("Status")
    plt.yticks([0, 1], ['OFF', 'ON'])
    plt.grid(True)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()
    return send_file(buf, mimetype='image/png')


@app.route('/data')
def view_data():
    data = load_data()
    lines = [f"{ts.isoformat()} -> {val}" for ts, val in sorted(data.items())]
    return "<pre>\n" + "\n".join(lines) + "\n</pre>"


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=23097)
