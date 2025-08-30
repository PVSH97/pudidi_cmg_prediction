"""
API endpoint for hydro optimization using CMG prices
WITH DETAILED DEBUGGING
"""

from http.server import BaseHTTPRequestHandler
import json
import numpy as np
from datetime import datetime, timedelta
import pytz
import traceback

# Import our cache manager
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api.utils.cache_manager_readonly import get_cached_data

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        try:
            print(f"[OPTIMIZER] POST request received at {datetime.now()}")
            
            # Parse request body
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            params = json.loads(post_data.decode('utf-8'))
            
            print(f"[OPTIMIZER] Received params: {json.dumps(params, indent=2)}")
            
            # Get optimization parameters
            horizon = params.get('horizon', 24)
            node = params.get('node', 'PMontt220')
            p_min = params.get('p_min', 0.5)
            p_max = params.get('p_max', 3.0)
            s0 = params.get('s0', 25000)
            s_min = params.get('s_min', 1000)
            s_max = params.get('s_max', 50000)
            kappa = params.get('kappa', 0.667)
            inflow = params.get('inflow', 1.1)
            
            print(f"[OPTIMIZER] Parameters extracted:")
            print(f"  - Horizon: {horizon} hours")
            print(f"  - Node: {node}")
            print(f"  - Power range: {p_min} - {p_max} MW")
            print(f"  - Storage: {s0} m³ (limits: {s_min} - {s_max})")
            print(f"  - Kappa (water/power): {kappa}")
            print(f"  - Inflow: {inflow} m³/s")
            
            # Get CMG prices from cache
            print(f"[OPTIMIZER] Fetching CMG data from cache...")
            cmg_data = get_cached_data()
            prices = []
            
            # Extract programmed prices
            if cmg_data and 'cmg_programmed' in cmg_data:
                print(f"[OPTIMIZER] Found {len(cmg_data['cmg_programmed'])} programmed prices in cache")
                for i, entry in enumerate(cmg_data['cmg_programmed'][:horizon]):
                    price = entry.get('cmg_programmed', 70)
                    prices.append(price)
                    if i < 5:  # Log first 5 prices
                        print(f"  Hour {i}: ${price:.2f}/MWh")
                if len(cmg_data['cmg_programmed']) > 5:
                    print(f"  ... and {len(cmg_data['cmg_programmed']) - 5} more")
            else:
                print(f"[OPTIMIZER] WARNING: No CMG data in cache, using synthetic prices")
            
            # Fill with synthetic if needed
            original_price_count = len(prices)
            while len(prices) < horizon:
                hour = len(prices) % 24
                base_price = 70
                variation = np.sin(hour * np.pi / 12) * 30
                prices.append(base_price + variation + np.random.random() * 10)
            
            if len(prices) > original_price_count:
                print(f"[OPTIMIZER] Added {len(prices) - original_price_count} synthetic prices to reach horizon")
            
            # Simple greedy optimization (simplified version)
            print(f"[OPTIMIZER] Starting optimization algorithm...")
            solution = self.optimize_hydro(
                prices, p_min, p_max, s0, s_min, s_max, kappa, inflow, horizon
            )
            
            print(f"[OPTIMIZER] Optimization complete!")
            print(f"  - Total revenue: ${solution['revenue']:.2f}")
            print(f"  - Avg generation: {solution['avg_generation']:.2f} MW")
            print(f"  - Peak generation: {solution['peak_generation']:.2f} MW")
            print(f"  - Capacity factor: {solution['capacity_factor']:.1f}%")
            
            # Send response
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            response = {
                'success': True,
                'solution': solution,
                'prices': prices,
                'parameters': {
                    'node': node,
                    'horizon': horizon,
                    'p_min': p_min,
                    'p_max': p_max,
                    's0': s0,
                    's_min': s_min,
                    's_max': s_max,
                    'kappa': kappa,
                    'inflow': inflow
                },
                'debug_info': {
                    'prices_from_cache': original_price_count,
                    'synthetic_prices': len(prices) - original_price_count,
                    'total_prices': len(prices)
                }
            }
            
            print(f"[OPTIMIZER] Sending response with {len(prices)} prices")
            self.wfile.write(json.dumps(response).encode())
            
        except Exception as e:
            print(f"[OPTIMIZER] ERROR: {str(e)}")
            print(f"[OPTIMIZER] Traceback: {traceback.format_exc()}")
            
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            error_response = {
                'success': False,
                'error': str(e),
                'traceback': traceback.format_exc()
            }
            self.wfile.write(json.dumps(error_response).encode())
    
    def optimize_hydro(self, prices, p_min, p_max, s0, s_min, s_max, kappa, inflow, horizon):
        """
        Simplified greedy optimization for demonstration
        In production, use proper LP solver
        """
        print(f"[OPTIMIZE] Starting hydro optimization with {len(prices)} price points")
        
        T = min(horizon, len(prices))
        dt = 1  # hourly steps
        vol_per_step = 3600 * dt
        
        print(f"[OPTIMIZE] Time horizon: {T} hours, volume per step: {vol_per_step} m³")
        
        # Initialize with minimum generation
        P = [p_min] * T
        print(f"[OPTIMIZE] Initialized all {T} hours with minimum generation: {p_min} MW")
        
        # Sort hours by price (greedy approach)
        price_indices = sorted(range(T), key=lambda i: prices[i], reverse=True)
        print(f"[OPTIMIZE] Top 5 price hours: {price_indices[:5]} with prices {[prices[i] for i in price_indices[:5]]}")
        
        # Try to maximize generation during high price hours
        changes_made = 0
        for rank, t in enumerate(price_indices):
            # Check if we can increase generation
            test_P = P.copy()
            test_P[t] = p_max
            
            # Check storage constraints
            storage_ok = True
            current_s = s0
            min_s_reached = s0
            max_s_reached = s0
            
            for i in range(T):
                current_s += (inflow - kappa * test_P[i]) * vol_per_step
                min_s_reached = min(min_s_reached, current_s)
                max_s_reached = max(max_s_reached, current_s)
                
                if current_s < s_min or current_s > s_max:
                    storage_ok = False
                    if rank < 10:  # Log why top 10 hours failed
                        print(f"[OPTIMIZE] Hour {t} (rank {rank+1}, price ${prices[t]:.2f}) failed: storage {current_s:.0f} at hour {i}")
                    break
            
            if storage_ok:
                P[t] = p_max
                changes_made += 1
                if changes_made <= 5:  # Log first 5 successful changes
                    print(f"[OPTIMIZE] Hour {t} (price ${prices[t]:.2f}): increased to {p_max} MW")
        
        print(f"[OPTIMIZE] Total changes made: {changes_made} hours increased to max power")
        
        # Calculate storage trajectory and metrics
        S = [s0]
        current_s = s0
        revenue = 0
        
        for t in range(T):
            Q = kappa * P[t]
            current_s += (inflow - Q) * vol_per_step
            S.append(current_s)
            revenue += prices[t] * P[t] * dt
        
        avg_gen = sum(P) / len(P)
        peak_gen = max(P)
        capacity = (avg_gen / p_max * 100)
        
        print(f"[OPTIMIZE] Final metrics:")
        print(f"  - Revenue: ${revenue:.2f}")
        print(f"  - Avg generation: {avg_gen:.2f} MW")
        print(f"  - Peak generation: {peak_gen:.2f} MW")
        print(f"  - Capacity factor: {capacity:.1f}%")
        print(f"  - Final storage: {S[-1]:.0f} m³")
        
        return {
            'P': P,  # Power generation (MW)
            'Q': [kappa * p for p in P],  # Water discharge (m3/s)
            'S': S,  # Storage levels (m3)
            'revenue': revenue,
            'avg_generation': avg_gen,
            'peak_generation': peak_gen,
            'capacity_factor': capacity
        }

    def do_GET(self):
        """Handle GET requests with query parameters"""
        try:
            # Parse query parameters
            from urllib.parse import urlparse, parse_qs
            parsed_url = urlparse(self.path)
            query_params = parse_qs(parsed_url.query)
            
            # Extract parameters with defaults
            params = {
                'horizon': int(query_params.get('horizon', [24])[0]),
                'node': query_params.get('node', ['PMontt220'])[0],
                'p_min': float(query_params.get('p_min', [0.5])[0]),
                'p_max': float(query_params.get('p_max', [3.0])[0]),
                's0': float(query_params.get('s0', [25000])[0]),
                's_min': float(query_params.get('s_min', [1000])[0]),
                's_max': float(query_params.get('s_max', [50000])[0]),
                'kappa': float(query_params.get('kappa', [0.667])[0]),
                'inflow': float(query_params.get('inflow', [1.1])[0])
            }
            
            # Get CMG prices
            cmg_data = get_cached_data()
            prices = []
            
            if cmg_data and 'cmg_programmed' in cmg_data:
                for entry in cmg_data['cmg_programmed'][:params['horizon']]:
                    prices.append(entry.get('cmg_programmed', 70))
            
            # Fill with synthetic if needed
            while len(prices) < params['horizon']:
                hour = len(prices) % 24
                base_price = 70
                variation = np.sin(hour * np.pi / 12) * 30
                prices.append(base_price + variation)
            
            # Run optimization
            solution = self.optimize_hydro(
                prices, 
                params['p_min'], params['p_max'],
                params['s0'], params['s_min'], params['s_max'],
                params['kappa'], params['inflow'], params['horizon']
            )
            
            # Send response
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            response = {
                'success': True,
                'solution': solution,
                'prices': prices,
                'parameters': params
            }
            
            self.wfile.write(json.dumps(response).encode())
            
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            error_response = {
                'success': False,
                'error': str(e)
            }
            self.wfile.write(json.dumps(error_response).encode())