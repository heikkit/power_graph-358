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
import socket

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

CONFIG = {
    'graph_file': 'power_graph.html',
    'data_file': 'power_data.json',
    'backup_file': 'power_data_backup.json',
    'update_interval': 5,
    'extra_wait': 60,
    'server_port': int(os.getenv('PORT', 10000))  # Render uses this
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
            x = [datetime.datetime.fromisoformat(ts) for ts in graph_data['x']]
            y = graph_data['y'][:]
        fig = go.Figure(data=go.Scatter(x=x, y=y, mode='lines+markers'))
        fig.update_layout(
            title='Outlet Power Status',
            xaxis_title='Time',
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
    now = datetime.datetime.now()
    next_mark = round_to_5min(now + datetime.timedelta(minutes=5))
    return (next_mark - now).total_seconds(), next_mark

def check_and_update_status():
    now = datetime.datetime.now()
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
        timestamp = request.form.get('timestamp')
        client_ip = request.form.get('client_ip', 'unknown')
        dt = datetime.datetime.fromisoformat(timestamp)
        rounded = round_to_5min(dt).isoformat()
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

def cleanup():
    stop_background_thread()
    save_data()

atexit.register(cleanup)

if __name__ == '__main__':
    load_data()
    start_background_thread()
    app.run(host='0.0.0.0', port=CONFIG['server_port'], threaded=True)
