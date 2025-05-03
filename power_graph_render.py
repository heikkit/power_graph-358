from flask import Flask, request, jsonify
import datetime
import time
import plotly.graph_objects as go
import threading
import os
import logging
import json
from collections import deque
import atexit
import pytz
import subprocess
import requests

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('power_graph.log')
    ]
)

graph_data = {'x': [], 'y': []}
graph_lock = threading.Lock()
recent_posts = deque(maxlen=50)
last_update_time = None
update_thread = None
shutdown_flag = threading.Event()

timezone = pytz.timezone('Europe/Helsinki')

CONFIG = {
    'graph_file': 'power_graph.html',
    'data_file': 'power_data.json',
    'backup_file': 'power_data_backup.json',
    'update_interval': 5,
    'extra_wait': 240,
    'grace_period_sec': 60,  # 1-minute grace period
    'server_port': int(os.getenv('PORT', 10000))
}

def round_to_5min(dt):
    minute = dt.minute
    rounded = minute - (minute % 5) if minute % 5 < 2.5 else minute + (5 - minute % 5)
    if rounded == 60:
        dt = dt.replace(minute=0) + datetime.timedelta(hours=1)
    else:
        dt = dt.replace(minute=rounded)
    return dt.replace(second=0, microsecond=0)

def update_graph():
    global last_update_time
    try:
        with graph_lock:
            x = [datetime.datetime.fromisoformat(ts).astimezone(timezone) for ts in graph_data['x']]
            y = graph_data['y'][:]
        fig = go.Figure(data=go.Scatter(x=x, y=y, mode='lines+markers'))
        fig.update_layout(
            title='Outlet Power Status',
            xaxis_title='Time (Europe/Helsinki)',
            yaxis_title='Power (0 or 1)',
            template='plotly_white'
        )
        fig.write_html(CONFIG['graph_file'])
        last_update_time = datetime.datetime.now()
        save_data()
    except Exception as e:
        logging.error(f'Graph update error: {e}')

def save_data():
    try:
        with graph_lock:
            with open(CONFIG['data_file'], 'w') as f:
                json.dump(graph_data, f)
    except Exception as e:
        logging.error(f'Data save error: {e}')

def load_data():
    try:
        if os.path.exists(CONFIG['data_file']):
            with open(CONFIG['data_file'], 'r') as f:
                data = json.load(f)
            if 'x' in data and 'y' in data:
                with graph_lock:
                    graph_data['x'] = data['x']
                    graph_data['y'] = data['y']
    except Exception as e:
        logging.error(f'Data load error: {e}')

def get_time_to_next_mark():
    now = datetime.datetime.now(datetime.timezone.utc)
    next_mark = round_to_5min(now + datetime.timedelta(minutes=5))
    return (next_mark - now).total_seconds(), next_mark

def check_and_update_status():
    now = datetime.datetime.now(datetime.timezone.utc)
    current_mark = round_to_5min(now)
    with graph_lock:
        if not graph_data['x'] or round_to_5min(datetime.datetime.fromisoformat(graph_data['x'][-1])) < current_mark:
            graph_data['x'].append(current_mark.isoformat())
            graph_data['y'].append(0)
            threading.Thread(target=update_graph, daemon=True).start()

def background_task():
    while not shutdown_flag.is_set():
        sleep_time, _ = get_time_to_next_mark()
        time.sleep(sleep_time + CONFIG['extra_wait'])
        check_and_update_status()

def start_background_thread():
    global update_thread
    if not update_thread or not update_thread.is_alive():
        update_thread = threading.Thread(target=background_task, daemon=True)
        update_thread.start()

def stop_background_thread():
    shutdown_flag.set()

@app.route('/power_status', methods=['POST'])
def power_status():
    try:
        received_at = datetime.datetime.now(datetime.timezone.utc)
        rounded = round_to_5min(received_at).isoformat()
        with graph_lock:
            if graph_data['x'] and round_to_5min(datetime.datetime.fromisoformat(graph_data['x'][-1])).isoformat() == rounded:
                graph_data['y'][-1] = 1
            else:
                graph_data['x'].append(rounded)
                graph_data['y'].append(1)
        threading.Thread(target=update_graph, daemon=True).start()
        return jsonify({'status': 'success'})
    except Exception as e:
        logging.error(f'POST error: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/')
def index():
    return '<meta http-equiv="refresh" content="0; url=/power_graph">'

@app.route('/power_graph')
def power_graph():
    return open(CONFIG['graph_file']).read() if os.path.exists(CONFIG['graph_file']) else 'Graph not ready.'

@app.route('/status')
def status():
    with graph_lock:
        data_points = len(graph_data['x'])
        latest = graph_data['x'][-1] if data_points else 'none'
    return jsonify({
        'points': data_points,
        'latest': latest,
        'last_update': last_update_time.isoformat() if last_update_time else 'never'
    })

@app.route('/status_text')
def status_text():
    try:
        with graph_lock:
            if not graph_data['x']:
                return '<p>Status unknown</p>'
            latest_dt_utc = datetime.datetime.fromisoformat(graph_data['x'][-1]).replace(tzinfo=datetime.timezone.utc)
            latest_local = latest_dt_utc.astimezone(timezone)
            latest_status = graph_data['y'][-1]

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        
        # Calculate the time of the next expected POST (next 5-minute mark after the latest data point)
        latest_dt_rounded = round_to_5min(latest_dt_utc)
        next_expected_post = latest_dt_rounded
        
        # If the latest data point is already at a 5-minute mark and has a status of 1
        # then the next expected post is 5 minutes later
        if latest_dt_rounded == latest_dt_utc and latest_status == 1:
            next_expected_post = latest_dt_rounded + datetime.timedelta(minutes=5)
        # If it's not at a 5-minute mark or status is 0, the next expected post is the next 5-minute mark
        elif latest_dt_rounded <= latest_dt_utc:
            next_expected_post = latest_dt_rounded + datetime.timedelta(minutes=5)
            
        # Add the grace period
        grace_period_end = next_expected_post + datetime.timedelta(seconds=CONFIG['grace_period_sec'])
        
        # Determine status
        if now_utc > grace_period_end:
            status_str = "OFF"
            color = "red"
        else:
            status_str = "ON" if latest_status == 1 else "OFF"
            color = "green" if latest_status == 1 else "red"

        time_str = latest_local.strftime("%H:%M")

        html = f"""
        <html><head><title>Power Outlet Status</title></head>
        <body style='font-family: sans-serif; text-align: center; padding-top: 50px;'>
            <h1 style='color: {color};'>Power outlet status: {status_str}</h1>
            <p>As of: {time_str} (Europe/Helsinki)</p>
        </body></html>
        """
        return html
    except Exception as e:
        logging.error(f'Status text error: {e}')
        return '<p>Error generating status</p>'

def cleanup():
    stop_background_thread()
    save_data()

atexit.register(cleanup)

if __name__ == '__main__':
    load_data()
    start_background_thread()
    app.run(host='0.0.0.0', port=CONFIG['server_port'], threaded=True)
