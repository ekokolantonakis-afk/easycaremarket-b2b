# app.py - Main Flask Application for DigitalOcean
import os
import sqlite3
import requests
import time
import json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from contextlib import contextmanager
import threading
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Database initialization
def init_database():
    """Initialize SQLite database with all required tables"""
    with sqlite3.connect('b2b_catalog.db') as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gtin TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                brand TEXT,
                category TEXT,
                base_price REAL NOT NULL,
                selling_price REAL NOT NULL,
                inventory INTEGER DEFAULT 0,
                supplier TEXT,
                description TEXT,
                image_url TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                active BOOLEAN DEFAULT 1
            );
            
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                business_name TEXT NOT NULL,
                contact_name TEXT,
                phone TEXT,
                address TEXT,
                city TEXT,
                postal_code TEXT,
                tax_id TEXT,
                discount_tier TEXT DEFAULT 'standard',
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                order_number TEXT UNIQUE NOT NULL,
                total_amount REAL NOT NULL,
                status TEXT DEFAULT 'pending',
                order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                delivery_date DATE,
                notes TEXT,
                FOREIGN KEY (customer_id) REFERENCES customers (id)
            );
            
            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price REAL NOT NULL,
                line_total REAL NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders (id),
                FOREIGN KEY (product_id) REFERENCES products (id)
            );
            
            CREATE TABLE IF NOT EXISTS sync_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_type TEXT NOT NULL,
                status TEXT NOT NULL,
                products_processed INTEGER DEFAULT 0,
                products_added INTEGER DEFAULT 0,
                products_updated INTEGER DEFAULT 0,
                errors TEXT,
                start_time TIMESTAMP,
                end_time TIMESTAMP,
                duration_seconds INTEGER
            );
            
            CREATE INDEX IF NOT EXISTS idx_products_gtin ON products(gtin);
            CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
            CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand);
            CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
            CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
        ''')
        logger.info("Database initialized successfully")

# Database context manager
@contextmanager
def get_db():
    conn = sqlite3.connect('b2b_catalog.db')
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

# PandoraBox API Integration Class
class PandoraBoxSync:
    def __init__(self):
        self.email = os.environ.get('PANDORABOX_EMAIL')
        self.password = os.environ.get('PANDORABOX_PASSWORD')
        self.base_url = os.environ.get('PANDORABOX_BASE_URL', 'https://api.qogita.com')
        self.session = requests.Session()
        self.access_token = None
        self.refresh_token = None
        
    def authenticate(self):
        """Authenticate with PandoraBox using email/password and get JWT tokens"""
        if not self.email or not self.password:
            return False, "Email and password not configured"
        
        try:
            response = self.session.post(
                f"{self.base_url}/auth/login/",
                json={
                    'email': self.email,
                    'password': self.password
                },
                timeout=30
            )
            response.raise_for_status()
            
            data = response.json()
            self.access_token = data.get('access')
            self.refresh_token = data.get('refresh')
            
            # Update session headers with access token
            self.session.headers.update({
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json',
                'User-Agent': 'EasyCareMarket-B2B/1.0'
            })
            
            return True, "Authentication successful"
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Authentication failed: {e}")
            return False, f"Authentication failed: {str(e)}"
    
    def test_connection(self):
        """Test PandoraBox API connectivity with authentication"""
        if not self.email or not self.password:
            return False, "Email and password not configured"
        
        # Try to authenticate first
        auth_success, auth_message = self.authenticate()
        if not auth_success:
            return False, auth_message
        
        try:
            # Test with a simple API call
            response = self.session.get(f"{self.base_url}/addresses/", timeout=10)
            return response.status_code == 200, f"Status: {response.status_code}"
        except Exception as e:
            return False, str(e)
    
    def fetch_products_batch(self, page=1, per_page=100, filters=None):
        """Fetch products from PandoraBox API in batches"""
        # Authenticate if not already done
        if not self.access_token:
            auth_success, _ = self.authenticate()
            if not auth_success:
                return None
        
        # Use the CSV download endpoint for bulk data
        params = {
            'page': page,
            'per_page': per_page,
            'in_stock_only': True
        }
        
        if filters:
            params.update(filters)
        
        try:
            # Try variants search endpoint first
            response = self.session.get(
                f"{self.base_url}/variants/search/",
                params=params,
                timeout=30
            )
            
            if response.status_code == 401:
                # Token expired, re-authenticate
                auth_success, _ = self.authenticate()
                if auth_success:
                    response = self.session.get(
                        f"{self.base_url}/variants/search/",
                        params=params,
                        timeout=30
                    )
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout fetching page {page}")
            return None
        except Exception as e:
            logger.error(f"Error fetching products page {page}: {e}")
            return None
    
    def sync_products(self, max_pages=20, categories=None):
        """Sync products with speed optimization and 10% markup"""
        if not self.email or not self.password:
            return {"error": "Qogita email and password not configured"}
        
        start_time = datetime.now()
        processed = added = updated = errors = 0
        
        # Log sync start
        with get_db() as conn:
            cursor = conn.execute('''
                INSERT INTO sync_logs (sync_type, status, start_time)
                VALUES (?, ?, ?)
            ''', ('pandorabox_full', 'running', start_time))
            sync_log_id = cursor.lastrowid
            conn.commit()
        
        try:
            # Authenticate first
            auth_success, auth_message = self.authenticate()
            if not auth_success:
                return {"error": f"Authentication failed: {auth_message}"}
            
            for page in range(1, max_pages + 1):
                logger.info(f"Syncing page {page}/{max_pages}")
                
                filters = {}
                if categories:
                    filters['categories'] = ','.join(categories)
                
                data = self.fetch_products_batch(page, filters=filters)
                if not data or not data.get('results'):
                    logger.info(f"No more products on page {page}, stopping sync")
                    break
                
                # Process products in batch
                with get_db() as conn:
                    for product in data['results']:
                        try:
                            processed += 1
                            
                            # Calculate 10% markup
                            base_price = float(product.get('price', 0))
                            selling_price = round(base_price * 1.10, 2)
                            
                            # Check if product exists
                            cursor = conn.execute(
                                'SELECT id FROM products WHERE gtin = ?',
                                (product.get('gtin'),)
                            )
                            existing = cursor.fetchone()
                            
                            product_data = (
                                product.get('gtin'),
                                product.get('name', ''),
                                product.get('brand', ''),
                                product.get('category', ''),
                                base_price,
                                selling_price,
                                product.get('inventory', 0),
                                product.get('supplier', ''),
                                product.get('description', ''),
                                product.get('image_url', ''),
                                datetime.now()
                            )
                            
                            if existing:
                                conn.execute('''
                                    UPDATE products SET
                                    name=?, brand=?, category=?, base_price=?, selling_price=?,
                                    inventory=?, supplier=?, description=?, image_url=?, last_updated=?
                                    WHERE gtin=?
                                ''', product_data[1:] + (product_data[0],))
                                updated += 1
                            else:
                                conn.execute('''
                                    INSERT INTO products 
                                    (gtin, name, brand, category, base_price, selling_price, inventory, supplier, description, image_url, last_updated)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                ''', product_data)
                                added += 1
                                
                        except Exception as e:
                            errors += 1
                            logger.error(f"Error processing product {product.get('gtin')}: {e}")
                    
                    conn.commit()
                
                # Rate limiting - be nice to Qogita API
                time.sleep(1)
                
        except Exception as e:
            logger.error(f"Sync failed: {e}")
            errors += 1
        
        # Update sync log
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        with get_db() as conn:
            conn.execute('''
                UPDATE sync_logs SET
                status=?, products_processed=?, products_added=?, products_updated=?,
                errors=?, end_time=?, duration_seconds=?
                WHERE id=?
            ''', (
                'completed' if errors == 0 else 'completed_with_errors',
                processed, added, updated, str(errors), end_time, int(duration), sync_log_id
            ))
            conn.commit()
        
        return {
            'status': 'completed',
            'processed': processed,
            'added': added,
            'updated': updated,
            'errors': errors,
            'duration_seconds': int(duration)
        }

# Initialize components
init_database()
pandora = PandoraBoxSync()

# Health and Status Endpoints
@app.route('/')
def home():
    return jsonify({
        'service': 'EasyCare Market B2B API',
        'status': 'running',
        'version': '1.0.0',
        'timestamp': datetime.now().isoformat(),
        'environment': 'production'
    })

@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'database': 'connected',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/status')
def api_status():
    """Comprehensive API status"""
    with get_db() as conn:
        cursor = conn.execute('SELECT COUNT(*) as count FROM products WHERE active = 1')
        product_count = cursor.fetchone()['count']
        
        cursor = conn.execute('SELECT COUNT(*) as count FROM customers WHERE status = "active"')
        customer_count = cursor.fetchone()['count']
        
        cursor = conn.execute('''
            SELECT * FROM sync_logs 
            ORDER BY start_time DESC LIMIT 1
        ''')
        last_sync = cursor.fetchone()
    
    pandora_connected, pandora_message = pandora.test_connection()
    
    return jsonify({
        'api_status': 'operational',
        'products_count': product_count,
        'customers_count': customer_count,
        'pandorabox_api': {
            'connected': pandora_connected,
            'message': pandora_message,
            'email_configured': bool(pandora.email)
        },
        'last_sync': dict(last_sync) if last_sync else None,
        'timestamp': datetime.now().isoformat()
    })

# Product Management Endpoints
@app.route('/api/products')
def get_products():
    """Get products with filtering, pagination and search"""
    page = int(request.args.get('page', 1))
    per_page = min(int(request.args.get('per_page', 50)), 100)
    search = request.args.get('search', '')
    category = request.args.get('category')
    brand = request.args.get('brand')
    min_price = request.args.get('min_price')
    max_price = request.args.get('max_price')
    in_stock_only = request.args.get('in_stock_only', 'false').lower() == 'true'
    
    offset = (page - 1) * per_page
    
    # Build query
    query = 'SELECT * FROM products WHERE active = 1'
    params = []
    
    if search:
        query += ' AND (name LIKE ? OR brand LIKE ? OR category LIKE ?)'
        search_param = f'%{search}%'
        params.extend([search_param, search_param, search_param])
    
    if category:
        query += ' AND category = ?'
        params.append(category)
    
    if brand:
        query += ' AND brand = ?'
        params.append(brand)
    
    if min_price:
        query += ' AND selling_price >= ?'
        params.append(float(min_price))
    
    if max_price:
        query += ' AND selling_price <= ?'
        params.append(float(max_price))
    
    if in_stock_only:
        query += ' AND inventory > 0'
    
    query += ' ORDER BY name LIMIT ? OFFSET ?'
    params.extend([per_page, offset])
    
    with get_db() as conn:
        cursor = conn.execute(query, params)
        products = [dict(row) for row in cursor.fetchall()]
        
        # Get total count for pagination
        count_query = query.replace('SELECT *', 'SELECT COUNT(*)').split('ORDER BY')[0]
        cursor = conn.execute(count_query, params[:-2])  # Remove LIMIT and OFFSET params
        total = cursor.fetchone()[0]
    
    return jsonify({
        'products': products,
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': total,
            'total_pages': (total + per_page - 1) // per_page
        },
        'filters_applied': {
            'search': search,
            'category': category,
            'brand': brand,
            'min_price': min_price,
            'max_price': max_price,
            'in_stock_only': in_stock_only
        }
    })

@app.route('/api/categories')
def get_categories():
    """Get all product categories with counts"""
    with get_db() as conn:
        cursor = conn.execute('''
            SELECT category, COUNT(*) as count 
            FROM products 
            WHERE active = 1 AND category IS NOT NULL AND category != '' 
            GROUP BY category 
            ORDER BY count DESC
        ''')
        categories = [dict(row) for row in cursor.fetchall()]
    
    return jsonify({'categories': categories})

@app.route('/api/brands')
def get_brands():
    """Get all brands with counts"""
    with get_db() as conn:
        cursor = conn.execute('''
            SELECT brand, COUNT(*) as count 
            FROM products 
            WHERE active = 1 AND brand IS NOT NULL AND brand != '' 
            GROUP BY brand 
            ORDER BY count DESC
        ''')
        brands = [dict(row) for row in cursor.fetchall()]
    
    return jsonify({'brands': brands})

# Customer Management
@app.route('/api/customers', methods=['GET', 'POST'])
def manage_customers():
    if request.method == 'GET':
        with get_db() as conn:
            cursor = conn.execute('''
                SELECT * FROM customers 
                WHERE status = 'active' 
                ORDER BY business_name
            ''')
            customers = [dict(row) for row in cursor.fetchall()]
        
        return jsonify({'customers': customers})
    
    elif request.method == 'POST':
        data = request.get_json()
        required_fields = ['email', 'business_name', 'contact_name']
        
        for field in required_fields:
            if not data.get(field):
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        with get_db() as conn:
            try:
                cursor = conn.execute('''
                    INSERT INTO customers 
                    (email, business_name, contact_name, phone, address, city, postal_code, tax_id, discount_tier)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    data['email'],
                    data['business_name'],
                    data['contact_name'],
                    data.get('phone', ''),
                    data.get('address', ''),
                    data.get('city', ''),
                    data.get('postal_code', ''),
                    data.get('tax_id', ''),
                    data.get('discount_tier', 'standard')
                ))
                customer_id = cursor.lastrowid
                conn.commit()
                
                return jsonify({
                    'success': True,
                    'customer_id': customer_id,
                    'message': 'Customer created successfully'
                })
                
            except sqlite3.IntegrityError:
                return jsonify({'error': 'Customer with this email already exists'}), 400

# Order Management
@app.route('/api/orders', methods=['POST'])
def create_order():
    """Create new B2B order"""
    data = request.get_json()
    
    required_fields = ['customer_id', 'items']
    for field in required_fields:
        if not data.get(field):
            return jsonify({'error': f'Missing required field: {field}'}), 400
    
    if not data['items']:
        return jsonify({'error': 'Order must contain at least one item'}), 400
    
    with get_db() as conn:
        try:
            # Generate order number
            order_number = f"BO{datetime.now().strftime('%Y%m%d%H%M%S')}"
            
            # Calculate total
            total_amount = 0
            order_items = []
            
            for item in data['items']:
                cursor = conn.execute(
                    'SELECT id, selling_price, inventory FROM products WHERE id = ?',
                    (item['product_id'],)
                )
                product = cursor.fetchone()
                
                if not product:
                    return jsonify({'error': f'Product {item["product_id"]} not found'}), 400
                
                quantity = int(item['quantity'])
                if quantity > product['inventory']:
                    return jsonify({'error': f'Insufficient inventory for product {item["product_id"]}'}), 400
                
                unit_price = product['selling_price']
                line_total = unit_price * quantity
                total_amount += line_total
                
                order_items.append({
                    'product_id': item['product_id'],
                    'quantity': quantity,
                    'unit_price': unit_price,
                    'line_total': line_total
                })
            
            # Create order
            cursor = conn.execute('''
                INSERT INTO orders (customer_id, order_number, total_amount, notes)
                VALUES (?, ?, ?, ?)
            ''', (
                data['customer_id'],
                order_number,
                total_amount,
                data.get('notes', '')
            ))
            order_id = cursor.lastrowid
            
            # Add order items
            for item in order_items:
                conn.execute('''
                    INSERT INTO order_items (order_id, product_id, quantity, unit_price, line_total)
                    VALUES (?, ?, ?, ?, ?)
                ''', (order_id, item['product_id'], item['quantity'], item['unit_price'], item['line_total']))
                
                # Update inventory
                conn.execute('''
                    UPDATE products SET inventory = inventory - ? WHERE id = ?
                ''', (item['quantity'], item['product_id']))
            
            conn.commit()
            
            return jsonify({
                'success': True,
                'order_id': order_id,
                'order_number': order_number,
                'total_amount': total_amount,
                'message': 'Order created successfully'
            })
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Error creating order: {e}")
            return jsonify({'error': 'Failed to create order'}), 500

# PandoraBox Sync Endpoints
@app.route('/api/sync/test')
def test_pandorabox():
    """Test PandoraBox API connection"""
    connected, message = pandora.test_connection()
    return jsonify({
        'pandorabox_connected': connected,
        'message': message,
        'email_configured': bool(pandora.email),
        'password_configured': bool(pandora.password),
        'base_url': pandora.base_url
    })

@app.route('/api/sync/start', methods=['POST'])
def start_sync():
    """Start PandoraBox product sync"""
    data = request.get_json() or {}
    max_pages = data.get('max_pages', 10)
    categories = data.get('categories')
    
    if not pandora.email or not pandora.password:
        return jsonify({'error': 'PandoraBox email and password not configured'}), 400
    
    # Run sync in background thread
    def run_sync():
        result = pandora.sync_products(max_pages=max_pages, categories=categories)
        logger.info(f"Sync completed: {result}")
    
    thread = threading.Thread(target=run_sync)
    thread.start()
    
    return jsonify({
        'message': 'Sync started in background',
        'max_pages': max_pages,
        'categories': categories
    })

@app.route('/api/sync/status')
def sync_status():
    """Get sync status and logs"""
    with get_db() as conn:
        cursor = conn.execute('''
            SELECT * FROM sync_logs 
            ORDER BY start_time DESC 
            LIMIT 10
        ''')
        logs = [dict(row) for row in cursor.fetchall()]
    
    return jsonify({
        'sync_logs': logs,
        'last_sync': logs[0] if logs else None
    })

# Development endpoints (remove in production)
@app.route('/api/dev/sample-data', methods=['POST'])
def add_sample_data():
    """Add sample data for testing"""
    sample_products = [
        ('1234567890123', 'Head & Shoulders Shampoo 400ml', 'Procter & Gamble', 'Hair Care', 7.50, 8.25, 150, 'Supplier A', 'Anti-dandruff shampoo'),
        ('1234567890124', 'Oral-B Pro Expert Toothbrush', 'Procter & Gamble', 'Oral Care', 3.80, 4.18, 89, 'Supplier B', 'Professional toothbrush'),
        ('1234567890125', 'Johnson Baby Powder 200g', 'Johnson & Johnson', 'Baby Care', 6.10, 6.71, 203, 'Supplier C', 'Gentle baby powder'),
        ('1234567890126', 'Colgate Total Toothpaste 75ml', 'Colgate-Palmolive', 'Oral Care', 3.45, 3.80, 175, 'Supplier D', 'Complete protection toothpaste'),
        ('1234567890127', 'Nivea Body Lotion 250ml', 'Nivea', 'Skin Care', 6.55, 7.21, 95, 'Supplier E', 'Moisturizing body lotion')
    ]
    
    with get_db() as conn:
        for product in sample_products:
            conn.execute('''
                INSERT OR REPLACE INTO products 
                (gtin, name, brand, category, base_price, selling_price, inventory, supplier, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', product)
        conn.commit()
    
    return jsonify({
        'success': True,
        'message': f'Added {len(sample_products)} sample products with 10% markup'
    })

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

# Application entry point
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    debug = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    
    logger.info(f"Starting EasyCare Market B2B API on port {port}")
    app.run(host='0.0.0.0', port=port, debug=debug)
