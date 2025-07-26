from flask import Flask, render_template, jsonify, request
import asyncio
import socketio
from datetime import datetime
import time
import json
import math
import threading
from typing import List, Dict, Tuple
import os
import logging
from logging.handlers import RotatingFileHandler

# Configure logging
if not os.path.exists('logs'):
    os.makedirs('logs')

logging.basicConfig(
    handlers=[RotatingFileHandler('logs/app.log', maxBytes=100000, backupCount=10)],
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(threadName)s : %(message)s'
)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# Global variables for tracking simulation state
simulation_state = {
    'mode1': {'active': False, 'thread': None, 'counter': 0},
    'mode2': {'active': False, 'thread': None, 'counter': 0}
}

# Socket.IO clients for each mode
sio_clients = {
    'mode1': None,
    'mode2': None
}

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great circle distance between two points on earth"""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    r = 6371
    return c * r

def calculate_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the bearing between two points"""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = math.atan2(y, x)
    bearing = math.degrees(bearing)
    bearing = (bearing + 360) % 360
    return bearing

def load_and_process_gps_data(filename: str, offset_coords: bool = False) -> List[Dict]:
    """Load GPS data from JSON file and calculate SOG, COG, HDG"""
    try:
        # Check if file exists
        if not os.path.exists(filename):
            app.logger.error(f"GPS data file not found: {filename}")
            return []
            
        with open(filename, 'r') as f:
            data = json.load(f)
        
        tracking_data = data['tracking_data']
        processed_data = []
        
        for i, point in enumerate(tracking_data):
            lat = point['latitude'] + (0.0001 if offset_coords else 0)
            lon = point['longitude'] + (0.0001 if offset_coords else 0)
            
            processed_point = {
                'timestamp': point['timestamp'],
                'latitude': lat,
                'longitude': lon,
                'interval_seconds': point['interval_seconds'],
                'sog': 0.0,
                'cog': 0.0,
                'hdg': 0
            }
            
            if i > 0:
                prev_point = processed_data[i-1]
                distance_km = haversine_distance(
                    prev_point['latitude'], prev_point['longitude'],
                    lat, lon
                )
                distance_nm = distance_km * 0.539957
                time_diff_hours = point['interval_seconds'] / 3600.0
                
                if time_diff_hours > 0:
                    sog = distance_nm / time_diff_hours
                else:
                    sog = 0.0
                
                cog = calculate_bearing(
                    prev_point['latitude'], prev_point['longitude'],
                    lat, lon
                )
                
                processed_point['sog'] = round(sog, 1)
                processed_point['cog'] = round(cog, 1)
                processed_point['hdg'] = int(round(cog))
            
            processed_data.append(processed_point)
        
        app.logger.info(f"Loaded {len(processed_data)} GPS points from {filename}")
        return processed_data
    except Exception as e:
        app.logger.error(f"Error loading GPS data: {e}")
        return []

def generate_ais_data(mmsi: str, gps_point: Dict):
    """Generate AIS data from GPS point"""
    return {
        "message": {
            "data": {
                "valid": True,
                "aistype": 1,
                "mmsi": mmsi,
                "navstatus": 0,
                "lon": round(gps_point['longitude'], 6),
                "lat": round(gps_point['latitude'], 6),
                "cog": gps_point['cog'],
                "sog": gps_point['sog'],
                "hdg": gps_point['hdg'],
                "utc": int(time.time()) % 60
            },
            "port": "COM3"
        },
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }

async def run_simulation_mode(mode: str):
    """Run simulation for specified mode"""
    global simulation_state, sio_clients
    
    # Create new socket client for this mode
    sio = socketio.AsyncClient()
    sio_clients[mode] = sio
    
    try:
        await sio.connect("http://ais-ws.agus-darmawan.com")
        app.logger.info(f"Connected to server for {mode}")
        
        # Load GPS data based on mode
        if mode == 'mode1':
            mmsi = "111111111"
            gps_data = load_and_process_gps_data("demo-data.json", offset_coords=False)
            interval_type = "original"
        else:  # mode2
            mmsi = "222222222"
            gps_data = load_and_process_gps_data("demo-data.json", offset_coords=True)
            interval_type = "realtime"
        
        if not gps_data:
            app.logger.error(f"No GPS data loaded for {mode}")
            return
        
        app.logger.info(f"Starting {mode} with {len(gps_data)} points")
        
        for i, gps_point in enumerate(gps_data):
            # Check if simulation should stop
            if not simulation_state[mode]['active']:
                break
                
            # Send AIS data
            data = generate_ais_data(mmsi, gps_point)
            await sio.emit("ais:data", data)
            
            # Update counter
            simulation_state[mode]['counter'] += 1
            
            app.logger.debug(f"[{mode}] Sent point {simulation_state[mode]['counter']}: "
                            f"Lat: {gps_point['latitude']:.6f}, "
                            f"Lon: {gps_point['longitude']:.6f}")
            
            # Wait for next interval
            if i < len(gps_data) - 1:
                if mode == 'mode1':
                    sleep_time = gps_data[i + 1]['interval_seconds']
                else:  # mode2
                    sleep_time = 5  # 5 seconds for realtime
                
                await asyncio.sleep(sleep_time)
        
        app.logger.info(f"{mode} simulation completed")
        
    except Exception as e:
        app.logger.error(f"Error in {mode}: {e}")
    finally:
        simulation_state[mode]['active'] = False
        if sio.connected:
            await sio.disconnect()

def run_async_simulation(mode: str):
    """Wrapper to run async simulation in thread"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_simulation_mode(mode))
    finally:
        loop.close()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/health')
def health_check():
    """Health check endpoint for monitoring"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'modes': {
            'mode1': simulation_state['mode1']['active'],
            'mode2': simulation_state['mode2']['active']
        }
    })

@app.route('/api/status')
def get_status():
    """Get current status of both modes"""
    return jsonify({
        'mode1': {
            'active': simulation_state['mode1']['active'],
            'counter': simulation_state['mode1']['counter']
        },
        'mode2': {
            'active': simulation_state['mode2']['active'],
            'counter': simulation_state['mode2']['counter']
        }
    })

@app.route('/api/toggle/<mode>', methods=['POST'])
def toggle_mode(mode):
    """Toggle simulation mode on/off"""
    global simulation_state
    
    if mode not in ['mode1', 'mode2']:
        return jsonify({'error': 'Invalid mode'}), 400
    
    if simulation_state[mode]['active']:
        # Stop the simulation
        simulation_state[mode]['active'] = False
        simulation_state[mode]['counter'] = 0
        app.logger.info(f"Stopping {mode}")
        
        return jsonify({
            'status': 'stopped',
            'mode': mode,
            'active': False,
            'counter': 0
        })
    else:
        # Start the simulation
        simulation_state[mode]['active'] = True
        simulation_state[mode]['counter'] = 0
        
        # Start simulation in background thread
        thread = threading.Thread(target=run_async_simulation, args=(mode,))
        thread.daemon = True
        thread.start()
        simulation_state[mode]['thread'] = thread
        
        app.logger.info(f"Starting {mode}")
        
        return jsonify({
            'status': 'started',
            'mode': mode,
            'active': True,
            'counter': 0
        })

@app.errorhandler(404)
def not_found_error(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    app.logger.error(f'Server Error: {error}')
    return jsonify({'error': 'Internal server error'}), 500

# Graceful shutdown handler
import signal
import sys

def signal_handler(sig, frame):
    app.logger.info('Shutting down gracefully...')
    # Stop all active simulations
    for mode in simulation_state:
        if simulation_state[mode]['active']:
            simulation_state[mode]['active'] = False
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False)