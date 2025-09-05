#!/usr/bin/env python3
"""
Smart CMG Online Update with Caching
Only fetches missing data, merges with existing cache
Uses the CORRECT v4 API endpoint that works
"""

import json
import requests
import time
from pathlib import Path
from datetime import datetime, timedelta
import pytz
from collections import defaultdict
import numpy as np

# Configuration
SIP_API_KEY = '1a81177c8ff4f69e7dd5bb8c61bc08b4'
SIP_BASE_URL = 'https://sipub.api.coordinador.cl:443'

# Our 3 target nodes for historical data
CMG_NODES = [
    'NVA_P.MONTT___220',
    'PIDPID________110', 
    'DALCAHUE______110'
]

# Cache configuration
CACHE_DIR = Path('data/cache')
CACHE_FILE = CACHE_DIR / 'cmg_historical_latest.json'
METADATA_FILE = CACHE_DIR / 'metadata.json'

def load_existing_cache():
    """Load existing cache data"""
    if not CACHE_FILE.exists():
        return {}, set()
    
    try:
        with open(CACHE_FILE, 'r') as f:
            cache_data = json.load(f)
        
        # Extract existing records as a set for quick lookup
        existing = set()
        for record in cache_data.get('data', []):
            key = f"{record['datetime']}_{record['node']}"
            existing.add(key)
        
        return cache_data, existing
    except Exception as e:
        print(f"Error loading cache: {e}")
        return {}, set()

def determine_missing_hours(existing_keys, target_dates, nodes):
    """Determine which date/hour/node combinations are missing"""
    missing = []
    santiago_tz = pytz.timezone('America/Santiago')
    
    for date_str in target_dates:
        for hour in range(24):
            dt = datetime.strptime(f"{date_str} {hour:02d}:00:00", '%Y-%m-%d %H:%M:%S')
            dt = santiago_tz.localize(dt)
            
            for node in nodes:
                datetime_str = dt.strftime('%Y-%m-%dT%H:%M:%S')
                key = f"{datetime_str}_{node}"
                
                if key not in existing_keys:
                    missing.append({
                        'date': date_str,
                        'hour': hour,
                        'node': node,
                        'datetime': datetime_str
                    })
    
    return missing

def fetch_page_with_retry(url, params, page_num, max_retries=10):
    """Fetch a single page with retry logic"""
    wait_time = 2
    
    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=60)
            
            if response.status_code == 200:
                data = response.json()
                records = data.get('data', [])
                return records, len(records)
            
            elif response.status_code == 502:
                wait_time = min(wait_time * 1.5, 60)
                print(f"      Page {page_num}: 502 error, attempt {attempt+1}/{max_retries}, waiting {wait_time:.1f}s")
                time.sleep(wait_time)
            
            elif response.status_code == 429:
                wait_time = min(wait_time * 2, 60)
                print(f"      Page {page_num}: Rate limited, attempt {attempt+1}/{max_retries}, waiting {wait_time:.1f}s")
                time.sleep(wait_time)
            
            elif response.status_code == 403:
                print(f"      Page {page_num}: 403 Forbidden - API key may be invalid")
                return [], 0
            
            else:
                print(f"      Page {page_num}: Error {response.status_code}, attempt {attempt+1}/{max_retries}")
                time.sleep(3)
        
        except requests.exceptions.Timeout:
            print(f"      Page {page_num}: Timeout, attempt {attempt+1}/{max_retries}")
            time.sleep(5)
        
        except Exception as e:
            print(f"      Page {page_num}: Exception {str(e)[:50]}, attempt {attempt+1}/{max_retries}")
            time.sleep(5)
    
    print(f"      Page {page_num}: Failed after {max_retries} attempts")
    return [], 0

def fetch_cmg_online_for_date(date_str, nodes):
    """Fetch CMG Online data using the CORRECT v4 API with proper pagination"""
    url = f"{SIP_BASE_URL}/costo-marginal-online/v4/findByDate"
    
    all_records = []
    page = 1
    consecutive_empty = 0
    location_hours = defaultdict(set)
    
    print(f"📊 Fetching {date_str}: targeting {len(nodes)} nodes")
    
    while consecutive_empty < 3:
        params = {
            'startDate': date_str,
            'endDate': date_str,
            'page': page,
            'limit': 4000,  # Use 4000 as per working implementation
            'user_key': SIP_API_KEY  # user_key, not api_key!
        }
        
        records, count = fetch_page_with_retry(url, params, page)
        
        if count > 0:
            consecutive_empty = 0
            
            # Filter for our nodes and process
            our_records = []
            for record in records:
                node = record.get('barra_transf', '')  # Use barra_transf field
                if node in nodes:
                    # Use hra field for hour (not hora)
                    hour = record.get('hra', 0)
                    
                    # Parse the record properly
                    parsed = parse_historical_record(record, date_str)
                    if parsed:
                        our_records.append(parsed)
                        location_hours[node].add(hour)
            
            all_records.extend(our_records)
            
            # Show progress
            unique_hours = set()
            for hours in location_hours.values():
                unique_hours.update(hours)
            
            if our_records:
                print(f"   Page {page:3d}: {count:4d} total, {len(our_records):3d} for our nodes, coverage: {len(unique_hours)}/24 hours")
            
            # Check if this is the last page
            if count < 4000:
                print(f"   Page {page} is the last page ({count} < 4000)")
                break
            
            page += 1
            
            # Safety limit
            if page > 50:
                print(f"   Reached page limit (50)")
                break
        else:
            consecutive_empty += 1
            if consecutive_empty >= 3:
                print(f"   End of data (3 consecutive empty pages)")
                break
            page += 1
    
    # Show coverage summary
    print(f"   Coverage summary:")
    for node in sorted(nodes):
        hours = sorted(location_hours.get(node, set()))
        coverage = len(hours)
        if coverage == 24:
            print(f"      ✅ {node}: {coverage}/24 hours")
        elif coverage > 0:
            print(f"      ⚠️ {node}: {coverage}/24 hours")
        else:
            print(f"      ❌ {node}: 0/24 hours")
    
    return all_records

def parse_historical_record(record, date_str):
    """Parse a historical CMG record from v4 API"""
    try:
        # Use 'hra' field for the actual hour
        hour = record.get('hra', 0)
        
        # cmg_clp_kwh_ is in CLP/kWh, convert to CLP/MWh (*1000)
        cmg_clp_kwh = float(record.get('cmg_clp_kwh_', 0) or 0)
        cmg_real = cmg_clp_kwh * 1000  # Convert to MWh
        
        # cmg_usd_mwh_ is already in USD/MWh
        cmg_usd = float(record.get('cmg_usd_mwh_', 0) or 0)
        
        # Create datetime
        santiago_tz = pytz.timezone('America/Santiago')
        dt = datetime.strptime(f"{date_str} {hour:02d}:00:00", '%Y-%m-%d %H:%M:%S')
        dt = santiago_tz.localize(dt)
        
        return {
            'datetime': dt.strftime('%Y-%m-%dT%H:%M:%S'),
            'date': date_str,
            'hour': hour,
            'node': record.get('barra_transf', ''),
            'cmg_real': cmg_real,
            'cmg_usd': cmg_usd
        }
    except Exception as e:
        return None

def fetch_missing_data(missing_hours):
    """Fetch only missing data from API"""
    if not missing_hours:
        print("✅ Cache is complete, no data to fetch")
        return []
    
    # Group by date for efficient API calls
    by_date = defaultdict(list)
    for item in missing_hours:
        by_date[item['date']].append(item)
    
    all_records = []
    
    for date_str, date_items in by_date.items():
        # Get unique nodes for this date
        nodes_needed = list(set(item['node'] for item in date_items))
        
        # Fetch data for this date
        records = fetch_cmg_online_for_date(date_str, nodes_needed)
        all_records.extend(records)
        
        # Brief pause between dates
        if len(by_date) > 1:
            time.sleep(1)
    
    return all_records

def merge_with_cache(cache_data, new_records):
    """Merge new records with existing cache"""
    if 'data' not in cache_data:
        cache_data['data'] = []
    
    # Create set of existing keys for deduplication
    existing_keys = set()
    for record in cache_data['data']:
        key = f"{record['datetime']}_{record['node']}"
        existing_keys.add(key)
    
    # Add new records
    added = 0
    for record in new_records:
        key = f"{record['datetime']}_{record['node']}"
        if key not in existing_keys:
            cache_data['data'].append(record)
            existing_keys.add(key)
            added += 1
    
    # Sort by datetime and node
    cache_data['data'].sort(key=lambda x: (x['datetime'], x['node']))
    
    # Update metadata
    if cache_data['data']:
        dates = sorted(set(r['date'] for r in cache_data['data']))
        cache_data['metadata'] = {
            'last_update': datetime.now(pytz.timezone('America/Santiago')).isoformat(),
            'total_records': len(cache_data['data']),
            'oldest_date': dates[0],
            'newest_date': dates[-1],
            'nodes': CMG_NODES
        }
    
    return cache_data, added

def main():
    """Main smart update function"""
    start_time = time.time()
    santiago_tz = pytz.timezone('America/Santiago')
    now = datetime.now(santiago_tz)
    
    print(f"{'='*60}")
    print(f"SMART CMG ONLINE UPDATE - {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"{'='*60}")
    
    # Load existing cache
    print("📂 Loading existing cache...")
    cache_data, existing_keys = load_existing_cache()
    print(f"   Found {len(existing_keys)} existing records")
    
    # Determine target dates (last 3 days including today)
    target_dates = []
    for days_back in range(3):
        date = (now - timedelta(days=days_back)).strftime('%Y-%m-%d')
        target_dates.append(date)
    target_dates.reverse()  # Oldest to newest
    
    print(f"📅 Target dates: {', '.join(target_dates)}")
    
    # Determine missing data
    print("🔍 Checking for missing data...")
    missing = determine_missing_hours(existing_keys, target_dates, CMG_NODES)
    print(f"   Need to fetch: {len(missing)} records")
    
    # Fetch only missing data
    if missing:
        new_records = fetch_missing_data(missing)
        print(f"✅ Fetched {len(new_records)} new records")
        
        # Merge with cache
        print("🔄 Merging with cache...")
        cache_data, added = merge_with_cache(cache_data, new_records)
        print(f"   Added {added} new records to cache")
    else:
        new_records = []
        added = 0
    
    # Save updated cache
    print("💾 Saving updated cache...")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache_data, f, indent=2)
    
    # Update metadata
    duration = time.time() - start_time
    metadata = {
        'timestamp': now.isoformat(),
        'last_update': now.isoformat(),
        'update_duration_seconds': duration,
        'records_fetched': len(new_records),
        'records_added': added,
        'total_cache_records': len(cache_data.get('data', [])),
        'cache_efficiency': f"{(1 - len(missing)/(3*24*3)) * 100:.1f}%" if missing else "100%"
    }
    
    with open(METADATA_FILE, 'w') as f:
        json.dump(metadata, f, indent=2)
    
    print(f"⏱️ Update completed in {duration:.1f} seconds")
    print(f"📊 Cache efficiency: {metadata['cache_efficiency']}")
    print(f"{'='*60}")
    
    return 0

if __name__ == "__main__":
    exit(main())