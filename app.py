from flask import Flask, jsonify, request
import os
import sqlite3
import requests
import time
from datetime import datetime
from contextlib import contextmanager

app = Flask(__name__)

# Database setup
def init_db():
    with sqlite3.connect('products.db') as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gtin TEXT UNIQUE,
                name TEXT,
                brand TEXT,
                category TEXT,
                price REAL,
                inventory INTEGER,
                supplier TEXT,
                last_updated TEXT
            );
            
            CREATE TABLE IF NOT EXISTS sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_date TEXT,
                products_synced INTEGER,
                status TEXT,
                notes TEXT
            );
        ''')

# Initialize database on startup
init_db()

@contextmanager
def get_db():
    conn = sqlite3.connect('products.db')
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

@app.route('/')
def home():
    return jsonify({
        'status': 'success',
        'message': 'B2B EasyCare Market API is running!',
        'timestamp': datetime.now().isoformat(),
        'environment': 'DigitalOcean App Platform'
    })

@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/test')
def api_test():
    with get_db() as conn:
        cursor = conn.execute('SELECT COUNT(*) as count FROM products')
        product_count = cursor.fetchone()['count']
    
    return jsonify({
        'api': 'working',
        'database': 'connected',
        'products_count': product_count,
        'qogita_ready': bool(os.getenv('QOGITA_API_KEY'))
    })

@app.route('/api/qogita-test')
def qogita_test():
    """Test Qogita API connection"""
    api_key = os.getenv('QOGITA_API_KEY')
    base_url = os.getenv('QOGITA_BASE_URL', 'https://api.qogita.com')
    
    if not api_key:
        return jsonify({
            'status': 'error',
            'message': 'QOGITA_API_KEY not configured'
        })
    
    try:
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
        
        # Test with a simple endpoint (adjust based on Qogita's actual API)
        response = requests.get(
            f"{base_url}/products",
            headers=headers,
            params={'per_page': 1},
            timeout=10
        )
        
        return jsonify({
            'status': 'success' if response.status_code == 200 else 'error',
            'status_code': response.status_code,
            'message': 'Qogita API connection successful' if response.status_code == 200 else 'Connection failed',
            'timestamp': datetime.now().isoformat()
        })
        
    except requests.exceptions.Timeout:
        return jsonify({
            'status': 'error',
            'message': 'Qogita API timeout'
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Connection error: {str(e)}'
        })

@app.route('/api/products')
def get_products():
    """Get products from database"""
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    brand = request.args.get('brand')
    category = request.args.get('category')
    
    offset = (page - 1) * per_page
    
    query = 'SELECT * FROM products WHERE 1=1'
    params = []
    
    if brand:
        query += ' AND brand = ?'
        params.append(brand)
    
    if category:
        query += ' AND category = ?'
        params.append(category)
    
    query += ' LIMIT ? OFFSET ?'
    params.extend([per_page, offset])
    
    with get_db() as conn:
        cursor = conn.execute(query, params)
        products = [dict(row) for row in cursor.fetchall()]
        
        # Get total count
        count_query = 'SELECT COUNT(*) as total FROM products WHERE 1=1'
        count_params = []
        if brand:
            count_query += ' AND brand = ?'
            count_params.append(brand)
        if category:
            count_query += ' AND category = ?'
            count_params.append(category)
            
        cursor = conn.execute(count_query, count_params)
        total = cursor.fetchone()['total']
    
    return jsonify({
        'products': products,
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': total,
            'total_pages': (total + per_page - 1) // per_page
        }
    })

@app.route('/api/sync-status')
def sync_status():
    """Check sync status"""
    with get_db() as conn:
        cursor = conn.execute('''
            SELECT * FROM sync_log 
            ORDER BY sync_date DESC 
            LIMIT 5
        ''')
        logs = [dict(row) for row in cursor.fetchall()]
        
        cursor = conn.execute('SELECT COUNT(*) as total FROM products')
        total_products = cursor.fetchone()['total']
    
    return jsonify({
        'total_products': total_products,
        'recent_syncs': logs,
        'last_sync': logs[0] if logs else None
    })

@app.route('/api/add-sample-data', methods=['POST'])
def add_sample_data():
    """Add sample data for testing"""
    sample_products = [
        ('1234567890123', 'Head & Shoulders Shampoo 400ml', 'Procter & Gamble', 'Hair Care', 8.50, 150, 'Supplier A'),
        ('1234567890124', 'Oral-B Pro Expert Toothbrush', 'Procter & Gamble', 'Oral Care', 4.20, 89, 'Supplier B'),
        ('1234567890125', 'Johnson Baby Powder 200g', 'Johnson & Johnson', 'Baby Care', 6.75, 203, 'Supplier C'),
        ('1234567890126', 'Colgate Total Toothpaste 75ml', 'Colgate-Palmolive', 'Oral Care', 3.80, 175, 'Supplier D'),
        ('1234567890127', 'Nivea Body Lotion 250ml', 'Nivea', 'Skin Care', 7.20, 95, 'Supplier E')
    ]
    
    with get_db() as conn:
        conn.executemany('''
            INSERT OR REPLACE INTO products (gtin, name, brand, category, price, inventory, supplier, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', [(p[0], p[1], p[2], p[3], p[4], p[5], p[6], datetime.now().isoformat()) for p in sample_products])
        conn.commit()
    
    return jsonify({
        'status': 'success',
        'message': f'Added {len(sample_products)} sample products',
        'timestamp': datetime.now().isoformat()
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
