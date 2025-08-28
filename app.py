#!/usr/bin/env python3
import os
from dotenv import load_dotenv

# Load environment variables for deployment
load_dotenv()
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
import sqlite3
import requests
import csv
import io
import json
from datetime import datetime, timedelta
import logging
from contextlib import contextmanager
import uuid
from urllib.parse import urlencode

app = Flask(__name__)
CORS(app)

# Configuration - UPDATED WITH YOUR ACTUAL CREDENTIALS
DB_PATH = 'pandora_fast.db'
SUPPLIER_USERNAME = os.getenv('PANDORA_USERNAME')  # Your PANDORABOX username
SUPPLIER_PASSWORD = 'easy6012606'  # Your PANDORABOX password
SUPPLIER_API_BASE = os.getenv('PANDORA_API_BASE', 'https://api.supplier.com')
DEFAULT_MARKUP = 10.0
TOKEN_FILE = 'pandora_tokens.json'

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Token storage
class TokenManager:
    def __init__(self):
        self.access_token = None
        self.refresh_token = None
        self.expires_at = None
        self.load_tokens()
    
    def load_tokens(self):
        """Load tokens from file"""
        try:
            if os.path.exists(TOKEN_FILE):
                with open(TOKEN_FILE, 'r') as f:
                    data = json.load(f)
                    self.access_token = data.get('access_token')
                    self.refresh_token = data.get('refresh_token')
                    if data.get('expires_at'):
                        self.expires_at = datetime.fromisoformat(data['expires_at'])
        except Exception as e:
            logger.error(f"Error loading tokens: {e}")
    
    def save_tokens(self, access_token, refresh_token, expires_in=3600):
        """Save tokens to file"""
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = datetime.now() + timedelta(seconds=expires_in)
        
        try:
            with open(TOKEN_FILE, 'w') as f:
                json.dump({
                    'access_token': access_token,
                    'refresh_token': refresh_token,
                    'expires_at': self.expires_at.isoformat()
                }, f)
        except Exception as e:
            logger.error(f"Error saving tokens: {e}")
    
    def is_token_valid(self):
        """Check if current token is valid"""
        if not self.access_token or not self.expires_at:
            return False
        return datetime.now() < self.expires_at - timedelta(minutes=5)  # 5 min buffer
    
    def get_valid_token(self):
        """Get a valid access token, refreshing if necessary"""
        if self.is_token_valid():
            return self.access_token
        
        if self.refresh_token:
            if self.refresh_access_token():
                return self.access_token
        
        # If refresh failed, try full login
        if self.authenticate():
            return self.access_token
        
        return None
    
    def authenticate(self):
        """Authenticate with PANDORABOX API"""
        try:
            response = requests.post(f'{SUPPLIER_API_BASE}/auth/login/', 
                json={
                    'email': SUPPLIER_USERNAME,
                    'password': SUPPLIER_PASSWORD
                },
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"PANDORABOX response data: {data}")
                self.save_tokens(
                    data['accessToken'],
                    data.get('refreshToken', None),
                    expires_in=data.get('expires_in', 3600)
                )
                logger.info("Successfully authenticated with PANDORABOX API")
                return True
            else:
                logger.error(f"Authentication failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            return False
    
    def refresh_access_token(self):
        """Refresh the access token"""
        try:
            response = requests.post(f'{SUPPLIER_API_BASE}/auth/refresh/',
                json={'refresh': self.refresh_token},
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                self.save_tokens(
                    data['accessToken'],
                    self.refresh_token,  # Keep existing refresh token
                    expires_in=data.get('expires_in', 3600)
                )
                logger.info("Successfully refreshed access token")
                return True
            else:
                logger.error(f"Token refresh failed: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Token refresh error: {e}")
            return False

# Initialize token manager
token_manager = TokenManager()

@contextmanager
def get_db():
    """Fast database connection with optimizations"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # SQLite optimizations for speed
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA cache_size=10000')
    conn.execute('PRAGMA temp_store=MEMORY')
    
    try:
        yield conn
    finally:
        conn.close()

def init_database():
    """Initialize database with optimized schema"""
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS products (
                gtin TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT,
                brand TEXT NOT NULL,
                price REAL NOT NULL,
                unit INTEGER,
                inventory INTEGER,
                product_link TEXT,
                supplier TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create indexes for fast lookups
        conn.execute('CREATE INDEX IF NOT EXISTS idx_brand ON products(brand)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_category ON products(category)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_inventory ON products(inventory)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_brand_inventory ON products(brand, inventory)')
        
        # Brands cache table for instant dropdown population
        conn.execute('''
            CREATE TABLE IF NOT EXISTS brands_cache (
                brand TEXT PRIMARY KEY,
                product_count INTEGER,
                in_stock_count INTEGER,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Order requests table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS order_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT UNIQUE NOT NULL,
                customer_name TEXT NOT NULL,
                customer_email TEXT NOT NULL,
                customer_phone TEXT,
                company_name TEXT,
                items TEXT NOT NULL,
                total_amount REAL,
                status TEXT DEFAULT 'pending',
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Sync log table for filtered sync tracking
        conn.execute('''
            CREATE TABLE IF NOT EXISTS sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sync_type TEXT,
                filters_applied TEXT,
                products_imported INTEGER,
                duration_seconds REAL,
                status TEXT
            )
        ''')
        
        conn.execute('CREATE INDEX IF NOT EXISTS idx_request_id ON order_requests(request_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_status ON order_requests(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_created_at ON order_requests(created_at)')
        
        conn.commit()
        logger.info("Database initialized with speed optimizations")

# Initialize database
try:
    init_database()
    logger.info("Database initialization completed")
except Exception as e:
    logger.error(f"Database initialization error: {e}")

@app.route('/api/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})

@app.route('/api/test-auth', methods=['POST'])
def test_auth():
    """Test PANDORABOX API authentication"""
    try:
        success = token_manager.authenticate()
        if success:
            return jsonify({
                'success': True,
                'message': 'Successfully authenticated with PANDORABOX API',
                'token_expires': token_manager.expires_at.isoformat() if token_manager.expires_at else None
            })
        else:
            return jsonify({'error': 'Authentication failed. Check your credentials.'}), 401
            
    except Exception as e:
        logger.error(f"Auth test error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/sync', methods=['POST'])
def sync_catalog():
    """Download and sync PANDORABOX catalog"""
    try:
        logger.info("Starting catalog sync from PANDORABOX...")
        
        # Get valid access token
        access_token = token_manager.get_valid_token()
        if not access_token:
            return jsonify({'error': 'Failed to authenticate with PANDORABOX API'}), 401
        
        # Download CSV with authentication
        headers = {
            'Authorization': f'Bearer {access_token}',
        }
        
        response = requests.get(f'{SUPPLIER_API_BASE}/variants/search/download/', 
                              headers=headers, timeout=300)
        
        if response.status_code == 401:
            # Token might be expired, try to refresh
            access_token = token_manager.get_valid_token()
            if access_token:
                headers['Authorization'] = f'Bearer {access_token}'
                response = requests.get(f'{SUPPLIER_API_BASE}/variants/search/download/', 
                                      headers=headers, timeout=300)
        
        if response.status_code != 200:
            return jsonify({'error': f'PANDORABOX API error: {response.status_code} - {response.text}'}), 500
        
        # Fast bulk import
        imported = bulk_import_csv(response.text)
        update_brands_cache()
        
        return jsonify({
            'success': True,
            'imported': imported,
            'message': f'Successfully synced {imported} products from PANDORABOX'
        })
        
    except Exception as e:
        logger.error(f"Sync error: {e}")
        return jsonify({'error': str(e)}), 500

def get_sync_log():
    """Get sync operation history"""
    try:
        limit = min(int(request.args.get('limit', 20)), 100)
        
        with get_db() as conn:
            cursor = conn.execute('''
                SELECT * FROM sync_log 
                ORDER BY sync_date DESC 
                LIMIT ?
            ''', [limit])
            
            logs = []
            for row in cursor.fetchall():
                try:
                    filters = json.loads(row['filters_applied']) if row['filters_applied'] else {}
                except:
                    filters = {}
                
                logs.append({
                    'id': row['id'],
                    'sync_date': row['sync_date'],
                    'sync_type': row['sync_type'],
                    'filters_applied': filters,
                    'products_imported': row['products_imported'],
                    'duration_seconds': row['duration_seconds'],
                    'status': row['status']
                })
        
        return jsonify({'sync_log': logs, 'count': len(logs)})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def bulk_import_csv(csv_content):
    """Optimized bulk import"""
    with get_db() as conn:
        conn.execute('DELETE FROM products')
        
        csv_reader = csv.DictReader(io.StringIO(csv_content))
        
        products = []
        for row in csv_reader:
            try:
                # Handle different possible price column names
                price_column = None
                for col in row.keys():
                    if 'price' in col.lower() and ('lowest' in col.lower() or 'inc' in col.lower()):
                        price_column = col
                        break
                
                if not price_column:
                    price_column = next((col for col in row.keys() if 'price' in col.lower()), None)
                
                price = 0.0
                if price_column and row.get(price_column):
                    price_str = str(row[price_column]).replace(',', '').replace('€', '').replace('$', '').strip()
                    if price_str:
                        price = float(price_str)
                
                inventory = 0
                if row.get('Inventory'):
                    try:
                        inventory = int(row['Inventory'])
                    except ValueError:
                        inventory = 0
                
                products.append((
                    row.get('GTIN', ''),
                    row.get('Name', ''),
                    row.get('Category', ''),
                    row.get('Brand', ''),
                    price,
                    int(row.get('Unit', '0')) if row.get('Unit') else 0,
                    inventory,
                    row.get('Product Link', ''),
                    row.get('Supplier', '')
                ))
            except (ValueError, KeyError) as e:
                logger.warning(f"Error processing row: {e}")
                continue
        
        conn.executemany('''
            INSERT OR REPLACE INTO products 
            (gtin, name, category, brand, price, unit, inventory, product_link, supplier)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', products)
        
        conn.commit()
        return len(products)

def update_brands_cache():
    """Update brands cache"""
    with get_db() as conn:
        conn.execute('DELETE FROM brands_cache')
        
        conn.execute('''
            INSERT INTO brands_cache (brand, product_count, in_stock_count)
            SELECT 
                brand,
                COUNT(*) as product_count,
                SUM(CASE WHEN inventory > 0 THEN 1 ELSE 0 END) as in_stock_count
            FROM products 
            WHERE brand IS NOT NULL AND brand != ''
            GROUP BY brand
            ORDER BY brand
        ''')
        
        conn.commit()

@app.route('/api/brands', methods=['GET'])
def get_brands():
    """Get brands list"""
    try:
        with get_db() as conn:
            cursor = conn.execute('''
                SELECT brand, product_count, in_stock_count 
                FROM brands_cache 
                ORDER BY brand
            ''')
            
            brands = [
                {
                    'brand': row['brand'],
                    'total_products': row['product_count'],
                    'in_stock_products': row['in_stock_count']
                }
                for row in cursor.fetchall()
            ]
            
        return jsonify({'brands': brands, 'count': len(brands)})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/products/<brand>', methods=['GET'])
def get_products_by_brand(brand):
    """Get products by brand"""
    try:
        in_stock_only = request.args.get('in_stock_only', 'true').lower() == 'true'
        limit = min(int(request.args.get('limit', 100)), 500)
        markup = float(request.args.get('markup', DEFAULT_MARKUP))
        
        with get_db() as conn:
            query = '''
                SELECT gtin, name, category, price, unit, inventory, product_link
                FROM products 
                WHERE brand = ?
            '''
            params = [brand]
            
            if in_stock_only:
                query += ' AND inventory > 0'
            
            query += ' ORDER BY name LIMIT ?'
            params.append(limit)
            
            cursor = conn.execute(query, params)
            
            products = []
            markup_multiplier = 1 + (markup / 100)
            
            for row in cursor.fetchall():
                original_price = float(row['price'])
                marked_up_price = round(original_price * markup_multiplier, 2)
                
                products.append({
                    'gtin': row['gtin'],
                    'name': row['name'],
                    'category': row['category'],
                    'original_price': original_price,
                    'selling_price': marked_up_price,
                    'unit': row['unit'],
                    'inventory': row['inventory'],
                    'in_stock': row['inventory'] > 0,
                    'product_link': row['product_link']
                })
        
        return jsonify({
            'products': products,
            'brand': brand,
            'returned_count': len(products)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/product/<gtin>', methods=['GET'])
def get_product_details(gtin):
    """Get product by GTIN"""
    try:
        markup = float(request.args.get('markup', DEFAULT_MARKUP))
        
        with get_db() as conn:
            cursor = conn.execute('SELECT * FROM products WHERE gtin = ?', [gtin])
            row = cursor.fetchone()
            
            if not row:
                return jsonify({'error': 'Product not found'}), 404
            
            original_price = float(row['price'])
            marked_up_price = round(original_price * (1 + markup / 100), 2)
            
            product = {
                'gtin': row['gtin'],
                'name': row['name'],
                'category': row['category'],
                'brand': row['brand'],
                'original_price': original_price,
                'selling_price': marked_up_price,
                'unit': row['unit'],
                'inventory': row['inventory'],
                'in_stock': row['inventory'] > 0,
                'product_link': row['product_link']
            }
            
        return jsonify({'product': product})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/quote-request', methods=['POST'])
def submit_quote_request():
    """Submit quote request"""
    try:
        data = request.get_json()
        
        # Validation
        required_fields = ['customer_name', 'customer_email', 'items']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Generate unique request ID
        request_id = f"QR-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
        
        # Calculate total
        total_amount = 0
        for item in data['items']:
            total_amount += float(item.get('price', 0)) * int(item.get('quantity', 0))
        
        # Store request
        with get_db() as conn:
            conn.execute('''
                INSERT INTO order_requests 
                (request_id, customer_name, customer_email, customer_phone, company_name, 
                 items, total_amount, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', [
                request_id,
                data['customer_name'],
                data['customer_email'],
                data.get('customer_phone', ''),
                data.get('company_name', ''),
                json.dumps(data['items']),
                total_amount,
                data.get('notes', '')
            ])
            conn.commit()
        
        return jsonify({
            'success': True,
            'request_id': request_id,
            'message': 'Quote request submitted successfully'
        })
        
    except Exception as e:
        logger.error(f"Quote request error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/quotes', methods=['GET'])
def get_quote_requests():
    """Get quote requests for admin"""
    try:
        status_filter = request.args.get('status', '')
        limit = min(int(request.args.get('limit', 50)), 100)
        
        with get_db() as conn:
            query = '''
                SELECT * FROM order_requests
            '''
            params = []
            
            if status_filter:
                query += ' WHERE status = ?'
                params.append(status_filter)
            
            query += ' ORDER BY created_at DESC LIMIT ?'
            params.append(limit)
            
            cursor = conn.execute(query, params)
            
            quotes = []
            for row in cursor.fetchall():
                quotes.append({
                    'id': row['id'],
                    'request_id': row['request_id'],
                    'customer_name': row['customer_name'],
                    'customer_email': row['customer_email'],
                    'customer_phone': row['customer_phone'],
                    'company_name': row['company_name'],
                    'items': json.loads(row['items']) if row['items'] else [],
                    'total_amount': row['total_amount'],
                    'status': row['status'],
                    'notes': row['notes'],
                    'created_at': row['created_at'],
                    'updated_at': row['updated_at']
                })
        
        return jsonify({'quotes': quotes, 'count': len(quotes)})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/quotes/<request_id>/status', methods=['PUT'])
def update_quote_status(request_id):
    """Update quote status"""
    try:
        data = request.get_json()
        new_status = data.get('status')
        
        if new_status not in ['pending', 'processing', 'quoted', 'completed', 'cancelled']:
            return jsonify({'error': 'Invalid status'}), 400
        
        with get_db() as conn:
            conn.execute('''
                UPDATE order_requests 
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE request_id = ?
            ''', [new_status, request_id])
            
            if conn.total_changes == 0:
                return jsonify({'error': 'Quote request not found'}), 404
            
            conn.commit()
        
        return jsonify({'success': True, 'message': 'Status updated successfully'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Get system statistics"""
    try:
        with get_db() as conn:
            # Product stats
            cursor = conn.execute('SELECT COUNT(*) as total FROM products')
            total_products = cursor.fetchone()['total']
            
            cursor = conn.execute('SELECT COUNT(*) as total FROM products WHERE inventory > 0')
            in_stock_products = cursor.fetchone()['total']
            
            cursor = conn.execute('SELECT COUNT(*) as total FROM brands_cache')
            total_brands = cursor.fetchone()['total']
            
            # Quote stats
            cursor = conn.execute('SELECT COUNT(*) as total FROM order_requests')
            total_quotes = cursor.fetchone()['total']
            
            cursor = conn.execute('SELECT COUNT(*) as total FROM order_requests WHERE status = "pending"')
            pending_quotes = cursor.fetchone()['total']
            
        return jsonify({
            'products': {
                'total': total_products,
                'in_stock': in_stock_products,
                'out_of_stock': total_products - in_stock_products
            },
            'brands': {'total': total_brands},
            'quotes': {
                'total': total_quotes,
                'pending': pending_quotes
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Simple HTML interfaces
CUSTOMER_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>B2B Catalog - EasyCare Market</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .header { text-align: center; margin-bottom: 30px; }
        .brand-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 15px; margin-bottom: 30px; }
        .brand-card { padding: 15px; border: 1px solid #ddd; border-radius: 5px; cursor: pointer; transition: all 0.3s; }
        .brand-card:hover { background: #f0f0f0; transform: translateY(-2px); }
        .product-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 15px; }
        .product-card { padding: 15px; border: 1px solid #ddd; border-radius: 5px; }
        .price { font-size: 18px; font-weight: bold; color: #007bff; }
        .btn { padding: 10px 15px; border: none; border-radius: 5px; cursor: pointer; margin: 5px; }
        .btn-primary { background: #007bff; color: white; }
        .btn-success { background: #28a745; color: white; }
        .cart { position: fixed; top: 20px; right: 20px; background: #007bff; color: white; padding: 10px; border-radius: 5px; }
        #loading { display: none; text-align: center; padding: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>EasyCare Market B2B Catalog</h1>
            <p>Professional wholesale prices for healthcare products</p>
        </div>
        
        <div class="cart">
            Cart: <span id="cart-count">0</span> items
            <button class="btn btn-success" onclick="showCart()">View Cart</button>
        </div>
        
        <div id="loading">Loading...</div>
        
        <div id="brands-section">
            <button class="btn btn-primary" onclick="loadBrands()">Load Brands</button>
            <div id="brands-grid" class="brand-grid"></div>
        </div>
        
        <div id="products-section" style="display:none;">
            <button class="btn" onclick="showBrands()">← Back to Brands</button>
            <h2 id="current-brand"></h2>
            <div id="products-grid" class="product-grid"></div>
        </div>
    </div>
    
    <script>
        let cart = [];
        let currentBrand = '';
        
        function showLoading(show) {
            document.getElementById('loading').style.display = show ? 'block' : 'none';
        }
        
        async function loadBrands() {
            showLoading(true);
            try {
                const response = await fetch('/api/brands');
                const data = await response.json();
                
                const grid = document.getElementById('brands-grid');
                grid.innerHTML = '';
                
                data.brands.forEach(brand => {
                    const card = document.createElement('div');
                    card.className = 'brand-card';
                    card.onclick = () => loadProducts(brand.brand);
                    card.innerHTML = `
                        <h3>${brand.brand}</h3>
                        <p>${brand.in_stock_products} products in stock</p>
                        <p>Total: ${brand.total_products} products</p>
                    `;
                    grid.appendChild(card);
                });
            } catch (error) {
                alert('Error loading brands: ' + error.message);
            }
            showLoading(false);
        }
        
        async function loadProducts(brand) {
            currentBrand = brand;
            showLoading(true);
            
            try {
                const response = await fetch(`/api/products/${encodeURIComponent(brand)}`);
                const data = await response.json();
                
                document.getElementById('brands-section').style.display = 'none';
                document.getElementById('products-section').style.display = 'block';
                document.getElementById('current-brand').textContent = brand;
                
                const grid = document.getElementById('products-grid');
                grid.innerHTML = '';
                
                data.products.forEach(product => {
                    const card = document.createElement('div');
                    card.className = 'product-card';
                    card.innerHTML = `
                        <h4>${product.name}</h4>
                        <p><strong>Category:</strong> ${product.category}</p>
                        <p><strong>Unit:</strong> ${product.unit}</p>
                        <p><strong>Stock:</strong> ${product.inventory}</p>
                        <p class="price">€${product.selling_price}</p>
                        <button class="btn btn-primary" onclick="addToCart('${product.gtin}', '${product.name}', ${product.selling_price})">
                            Add to Cart
                        </button>
                    `;
                    grid.appendChild(card);
                });
            } catch (error) {
                alert('Error loading products: ' + error.message);
            }
            showLoading(false);
        }
        
        function showBrands() {
            document.getElementById('brands-section').style.display = 'block';
            document.getElementById('products-section').style.display = 'none';
        }
        
        function addToCart(gtin, name, price) {
            const existing = cart.find(item => item.gtin === gtin);
            if (existing) {
                existing.quantity++;
            } else {
                cart.push({ gtin, name, price, quantity: 1 });
            }
            updateCartDisplay();
        }
        
        function updateCartDisplay() {
            document.getElementById('cart-count').textContent = cart.reduce((sum, item) => sum + item.quantity, 0);
        }
        
        function showCart() {
            if (cart.length === 0) {
                alert('Cart is empty');
                return;
            }
            
            let cartHtml = '<h3>Your Quote Request</h3>';
            let total = 0;
            
            cart.forEach(item => {
                const itemTotal = item.price * item.quantity;
                total += itemTotal;
                cartHtml += `<p>${item.name} - Qty: ${item.quantity} - €${itemTotal.toFixed(2)}</p>`;
            });
            
            cartHtml += `<p><strong>Total: €${total.toFixed(2)}</strong></p>`;
            cartHtml += '<button onclick="requestQuote()">Request Quote</button>';
            
            const popup = window.open('', 'cart', 'width=500,height=600');
            popup.document.write(cartHtml);
        }
        
        function requestQuote() {
            const name = prompt('Your Name:');
            const email = prompt('Your Email:');
            const company = prompt('Company Name (optional):');
            
            if (!name || !email) {
                alert('Name and email are required');
                return;
            }
            
            fetch('/api/quote-request', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    customer_name: name,
                    customer_email: email,
                    company_name: company || '',
                    items: cart
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    alert(`Quote request submitted! Reference: ${data.request_id}`);
                    cart = [];
                    updateCartDisplay();
                } else {
                    alert('Error: ' + data.error);
                }
            });
        }
    </script>
</body>
</html>
'''

ADMIN_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>Admin Dashboard - PANDORABOX Integration</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; }
        .card { background: white; padding: 20px; margin: 20px 0; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .btn { padding: 10px 15px; border: none; border-radius: 5px; cursor: pointer; margin: 5px; }
        .btn-primary { background: #007bff; color: white; }
        .btn-success { background: #28a745; color: white; }
        .btn-warning { background: #ffc107; color: black; }
        .btn-danger { background: #dc3545; color: white; }
        .btn-info { background: #17a2b8; color: white; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; }
        .stat-card { text-align: center; padding: 20px; background: #f8f9fa; border-radius: 5px; }
        .quote-card { border-left: 4px solid #007bff; padding: 15px; margin: 10px 0; }
        .log-entry { border-left: 4px solid #28a745; padding: 10px; margin: 5px 0; background: #f8f9fa; }
        .log-error { border-left-color: #dc3545; }
        #status { margin: 10px 0; padding: 10px; border-radius: 5px; }
        .success { background: #d4edda; color: #155724; }
        .error { background: #f8d7da; color: #721c24; }
        .loading { background: #cce5ff; color: #004085; }
        .filter-section { background: #f8f9fa; padding: 15px; border-radius: 5px; margin: 10px 0; }
        .filter-group { margin: 10px 0; }
        .filter-input { width: 200px; padding: 5px; margin: 5px; border: 1px solid #ddd; border-radius: 3px; }
        .checkbox-group { display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0; }
        .checkbox-item { display: flex; align-items: center; }
        .hidden { display: none; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h1>Enhanced PANDORABOX Integration Admin Dashboard</h1>
            <div id="status"></div>
        </div>
        
        <div class="card">
            <h2>API Management</h2>
            <button class="btn btn-primary" onclick="testAuth()">Test API Authentication</button>
            <button class="btn btn-success" onclick="syncCatalog()">Sync Full Catalog</button>
            <button class="btn btn-info" onclick="toggleFilteredSync()">Filtered Sync</button>
            <button class="btn btn-warning" onclick="loadStats()">Refresh Stats</button>
            <button class="btn btn-info" onclick="loadSyncLog()">View Sync History</button>
            
            <!-- Filtered Sync Section -->
            <div id="filtered-sync-section" class="filter-section hidden">
                <h3>Filtered Sync Options</h3>
                <p>Apply filters to sync only specific products from PANDORABOX catalog:</p>
                
                <div class="filter-group">
                    <label><strong>Brands (comma-separated):</strong></label><br>
                    <input type="text" id="filter-brands" class="filter-input" placeholder="e.g., Colgate,Oral-B,Sensodyne">
                    <small>Leave empty to include all brands</small>
                </div>
                
                <div class="filter-group">
                    <label><strong>Categories (comma-separated):</strong></label><br>
                    <input type="text" id="filter-categories" class="filter-input" placeholder="e.g., Oral Care,Personal Care">
                    <small>Leave empty to include all categories</small>
                </div>
                
                <div class="filter-group">
                    <label><strong>Price Range:</strong></label><br>
                    Min: <input type="number" id="filter-min-price" class="filter-input" step="0.01" placeholder="0.00">
                    Max: <input type="number" id="filter-max-price" class="filter-input" step="0.01" placeholder="1000.00">
                </div>
                
                <div class="filter-group">
                    <div class="checkbox-item">
                        <input type="checkbox" id="filter-in-stock" checked>
                        <label for="filter-in-stock">Only products with inventory (recommended)</label>
                    </div>
                </div>
                
                <button class="btn btn-success" onclick="syncWithFilters()">Start Filtered Sync</button>
                <button class="btn btn-warning" onclick="clearFilters()">Clear Filters</button>
            </div>
        </div>
        
        <div class="card">
            <h2>System Statistics</h2>
            <div id="stats" class="stats-grid">
                <div class="stat-card">Click "Refresh Stats" to load</div>
            </div>
        </div>
        
        <div class="card">
            <h2>Quote Requests</h2>
            <button class="btn btn-primary" onclick="loadQuotes()">Load Quotes</button>
            <div id="quotes"></div>
        </div>
        
        <div class="card">
            <h2>Sync History</h2>
            <div id="sync-log"></div>
        </div>
    </div>
    
    <script>
        function showStatus(message, type = 'loading') {
            const statusDiv = document.getElementById('status');
            statusDiv.innerHTML = message;
            statusDiv.className = type;
        }
        
        function toggleFilteredSync() {
            const section = document.getElementById('filtered-sync-section');
            section.classList.toggle('hidden');
        }
        
        function clearFilters() {
            document.getElementById('filter-brands').value = '';
            document.getElementById('filter-categories').value = '';
            document.getElementById('filter-min-price').value = '';
            document.getElementById('filter-max-price').value = '';
            document.getElementById('filter-in-stock').checked = true;
        }
        
        async function testAuth() {
            showStatus('Testing API authentication...', 'loading');
            
            try {
                const response = await fetch('/api/test-auth', { method: 'POST' });
                const data = await response.json();
                
                if (data.success) {
                    showStatus(`Authentication successful! Token expires: ${new Date(data.token_expires).toLocaleString()}`, 'success');
                } else {
                    showStatus(`Authentication failed: ${data.error}`, 'error');
                }
            } catch (error) {
                showStatus(`Error: ${error.message}`, 'error');
            }
        }
        
        async function syncCatalog() {
            showStatus('Syncing full catalog from PANDORABOX... This may take several minutes.', 'loading');
            
            try {
                const response = await fetch('/api/sync', { method: 'POST' });
                const data = await response.json();
                
                if (data.success) {
                    showStatus(`Full sync completed! Imported ${data.imported} products`, 'success');
                    loadStats();
                    loadSyncLog();
                } else {
                    showStatus(`Sync failed: ${data.error}`, 'error');
                }
            } catch (error) {
                showStatus(`Error: ${error.message}`, 'error');
            }
        }
        
        async function syncWithFilters() {
            const brands = document.getElementById('filter-brands').value.trim();
            const categories = document.getElementById('filter-categories').value.trim();
            const minPrice = document.getElementById('filter-min-price').value;
            const maxPrice = document.getElementById('filter-max-price').value;
            const inStockOnly = document.getElementById('filter-in-stock').checked;
            
            const filters = {};
            
            if (brands) {
                filters.brands = brands.split(',').map(b => b.trim()).filter(b => b);
            }
            
            if (categories) {
                filters.categories = categories.split(',').map(c => c.trim()).filter(c => c);
            }
            
            if (minPrice) {
                filters.min_price = parseFloat(minPrice);
            }
            
            if (maxPrice) {
                filters.max_price = parseFloat(maxPrice);
            }
            
            if (inStockOnly) {
                filters.in_stock_only = true;
            }
            
            let filterText = 'No specific filters';
            if (Object.keys(filters).length > 0) {
                const filterParts = [];
                if (filters.brands) filterParts.push(`Brands: ${filters.brands.join(', ')}`);
                if (filters.categories) filterParts.push(`Categories: ${filters.categories.join(', ')}`);
                if (filters.min_price) filterParts.push(`Min Price: €${filters.min_price}`);
                if (filters.max_price) filterParts.push(`Max Price: €${filters.max_price}`);
                if (filters.in_stock_only) filterParts.push('In-stock only');
                filterText = filterParts.join('; ');
            }
            
            showStatus(`Starting filtered sync... Filters: ${filterText}`, 'loading');
            
            try {
                const response = await fetch('/api/sync-filtered', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ filters: filters })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showStatus(`Filtered sync completed! Imported ${data.imported} products in ${data.duration_seconds}s`, 'success');
                    loadStats();
                    loadSyncLog();
                } else {
                    showStatus(`Filtered sync failed: ${data.error}`, 'error');
                }
            } catch (error) {
                showStatus(`Error: ${error.message}`, 'error');
            }
        }
        
        async function loadStats() {
            try {
                const response = await fetch('/api/stats');
                const data = await response.json();
                
                document.getElementById('stats').innerHTML = `
                    <div class="stat-card">
                        <h3>Products</h3>
                        <p>Total: ${data.products.total}</p>
                        <p>In Stock: ${data.products.in_stock}</p>
                        <p>Out of Stock: ${data.products.out_of_stock}</p>
                    </div>
                    <div class="stat-card">
                        <h3>Brands</h3>
                        <p>Total: ${data.brands.total}</p>
                    </div>
                    <div class="stat-card">
                        <h3>Quotes</h3>
                        <p>Total: ${data.quotes.total}</p>
                        <p>Pending: ${data.quotes.pending}</p>
                    </div>
                `;
            } catch (error) {
                showStatus(`Error loading stats: ${error.message}`, 'error');
            }
        }
        
        async function loadSyncLog() {
            try {
                const response = await fetch('/api/sync-log');
                const data = await response.json();
                
                const logDiv = document.getElementById('sync-log');
                if (data.sync_log && data.sync_log.length > 0) {
                    logDiv.innerHTML = data.sync_log.map(log => {
                        const isError = log.status.includes('error');
                        const filtersText = Object.keys(log.filters_applied).length > 0 
                            ? JSON.stringify(log.filters_applied, null, 2)
                            : 'No filters';
                        
                        return `
                            <div class="log-entry ${isError ? 'log-error' : ''}">
                                <h4>${log.sync_type} sync - ${new Date(log.sync_date).toLocaleString()}</h4>
                                <p><strong>Status:</strong> ${log.status}</p>
                                <p><strong>Products:</strong> ${log.products_imported}</p>
                                <p><strong>Duration:</strong> ${log.duration_seconds}s</p>
                                <details>
                                    <summary>Filters Applied</summary>
                                    <pre>${filtersText}</pre>
                                </details>
                            </div>
                        `;
                    }).join('');
                } else {
                    logDiv.innerHTML = '<p>No sync history available</p>';
                }
            } catch (error) {
                document.getElementById('sync-log').innerHTML = `<p>Error loading sync log: ${error.message}</p>`;
            }
        }
        
        async function loadQuotes() {
            try {
                const response = await fetch('/api/admin/quotes');
                const data = await response.json();
                
                const quotesDiv = document.getElementById('quotes');
                quotesDiv.innerHTML = '';
                
                data.quotes.forEach(quote => {
                    const quoteDiv = document.createElement('div');
                    quoteDiv.className = 'quote-card';
                    
                    const itemsHtml = quote.items.map(item => 
                        `${item.name} (${item.quantity}x €${item.price})`
                    ).join('<br>');
                    
                    quoteDiv.innerHTML = `
                        <h4>Request ${quote.request_id}</h4>
                        <p><strong>Customer:</strong> ${quote.customer_name} (${quote.customer_email})</p>
                        <p><strong>Company:</strong> ${quote.company_name || 'N/A'}</p>
                        <p><strong>Phone:</strong> ${quote.customer_phone || 'N/A'}</p>
                        <p><strong>Total:</strong> €${quote.total_amount}</p>
                        <p><strong>Status:</strong> ${quote.status}</p>
                        <p><strong>Items:</strong><br>${itemsHtml}</p>
                        <p><strong>Date:</strong> ${new Date(quote.created_at).toLocaleString()}</p>
                        <select onchange="updateStatus('${quote.request_id}', this.value)">
                            <option value="pending" ${quote.status === 'pending' ? 'selected' : ''}>Pending</option>
                            <option value="processing" ${quote.status === 'processing' ? 'selected' : ''}>Processing</option>
                            <option value="quoted" ${quote.status === 'quoted' ? 'selected' : ''}>Quoted</option>
                            <option value="completed" ${quote.status === 'completed' ? 'selected' : ''}>Completed</option>
                            <option value="cancelled" ${quote.status === 'cancelled' ? 'selected' : ''}>Cancelled</option>
                        </select>
                    `;
                    quotesDiv.appendChild(quoteDiv);
                });
            } catch (error) {
                showStatus(`Error loading quotes: ${error.message}`, 'error');
            }
        }
        
        async function updateStatus(requestId, newStatus) {
            try {
                const response = await fetch(`/api/admin/quotes/${requestId}/status`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status: newStatus })
                });
                
                if (response.ok) {
                    showStatus(`Status updated for ${requestId}`, 'success');
                } else {
                    showStatus('Error updating status', 'error');
                }
            } catch (error) {
                showStatus(`Error: ${error.message}`, 'error');
            }
        }
        
        // Load stats and sync log on page load
        loadStats();
        loadSyncLog();
    </script>
</body>
</html>
'''

@app.route('/')
def customer_interface():
    """Customer-facing interface"""
    return CUSTOMER_HTML

@app.route('/api/sync-filtered', methods=['POST'])
def sync_filtered():
    """Sync products with filters to reduce dataset size"""
    sync_start = datetime.now()
    try:
        data = request.get_json() or {}
        filters = data.get('filters', {})
        
        # Build query parameters for PANDORABOX API
        query_params = {}
        
        if filters.get('in_stock_only'):
            query_params['min_inventory'] = 1
            
        if filters.get('category'):
            query_params['category'] = filters['category']
            
        if filters.get('brand'):
            query_params['brand'] = filters['brand']
            
        if filters.get('price_min'):
            query_params['price_min'] = filters['price_min']
            
        if filters.get('price_max'):
            query_params['price_max'] = filters['price_max']
        
        # Construct the filtered URL
        base_url = f"{SUPPLIER_API_BASE}/variants/search/download/"
        if query_params:
            url = base_url + "?" + urlencode(query_params)
        else:
            url = base_url
        
        logger.info(f"Starting filtered sync with URL: {url}")
        
        # Get valid access token (same as sync_catalog)
        access_token = token_manager.get_valid_token()
        if not access_token:
            return jsonify({'error': 'Failed to authenticate with PANDORABOX API'}), 401
        
        # Download CSV with authentication
        headers = {
            'Authorization': f'Bearer {access_token}',
        }
        response = requests.get(url, headers=headers, timeout=300)
        
        if response.status_code == 401:
            # Token might be expired, try to refresh
            access_token = token_manager.get_valid_token()
            if access_token:
                headers['Authorization'] = f'Bearer {access_token}'
                response = requests.get(url, headers=headers, timeout=300)
        
        if response.status_code != 200:
            return jsonify({'error': f'PANDORABOX API error: {response.status_code} - {response.text}'}), 500
        
        # Fast bulk import
        imported = bulk_import_csv(response.text)
        update_brands_cache()
        
        sync_end = datetime.now()
        duration = (sync_end - sync_start).total_seconds()
        
        return jsonify({
            'success': True,
            'imported': imported,
            'duration_seconds': duration,
            'filters_applied': query_params,
            'message': f'Successfully synced {imported} products from PANDORABOX with filters'
        })
        
    except Exception as e:
        logger.error(f"Filtered sync error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/admin')
def admin_interface():
    """Admin interface"""
    return ADMIN_HTML

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
