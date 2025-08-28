# EasyCare Market B2B Platform

A comprehensive B2B wholesale platform for EasyCare Market with integrated supplier management, customer ordering, and automated inventory synchronization.

## Features

- **Product Catalog Management** - Browse and search wholesale products with advanced filtering
- **B2B Customer Accounts** - Professional customer registration and account management  
- **Order Processing** - Complete order workflow with inventory tracking
- **Supplier Integration** - Automated product synchronization with 10% markup
- **RESTful API** - Complete API for frontend integration and mobile apps
- **Admin Dashboard** - Comprehensive management interface

## Technology Stack

- **Backend**: Python Flask
- **Database**: SQLite (production-ready for medium scale)
- **Authentication**: JWT-based supplier API integration
- **Deployment**: DigitalOcean App Platform
- **Frontend**: Static HTML/JavaScript (admin interface)

## Installation

### Prerequisites
- Python 3.11+
- DigitalOcean account (for deployment)
- Supplier API credentials

### Local Development
```bash
git clone https://github.com/ekokolantonakis-afk/easycaremarket-b2b.git
cd easycaremarket-b2b
pip install -r requirements.txt
python app.py
```

### Production Deployment
This application is designed for DigitalOcean App Platform:

1. Fork this repository
2. Connect to DigitalOcean App Platform
3. Set environment variables (see Configuration)
4. Deploy automatically

## Configuration

Set these environment variables in your deployment platform:

```env
PANDORABOX_EMAIL=your_supplier_email
PANDORABOX_PASSWORD=your_supplier_password
PANDORABOX_BASE_URL=https://api.supplier.com
FLASK_DEBUG=False
```

## API Endpoints

### Public Endpoints
- `GET /` - Service status
- `GET /health` - Health check
- `GET /api/status` - System status and statistics

### Product Management
- `GET /api/products` - List products with filtering and pagination
- `GET /api/categories` - Product categories with counts
- `GET /api/brands` - Product brands with counts

### Customer Management  
- `GET /api/customers` - List customers
- `POST /api/customers` - Create new customer account

### Order Processing
- `POST /api/orders` - Create new order with inventory updates

### Sync Management
- `GET /api/sync/test` - Test supplier API connection
- `POST /api/sync/start` - Start product synchronization
- `GET /api/sync/status` - View synchronization logs

### Development Tools
- `POST /api/dev/sample-data` - Add sample products for testing

## Usage Examples

### Get Products with Filtering
```bash
curl "https://your-app.ondigitalocean.app/api/products?category=Oral%20Care&brand=Colgate&min_price=2.00&max_price=10.00&in_stock_only=true"
```

### Create Customer Account
```bash
curl -X POST https://your-app.ondigitalocean.app/api/customers \
  -H "Content-Type: application/json" \
  -d '{
    "email": "customer@example.com",
    "business_name": "Example Pharmacy",
    "contact_name": "John Smith",
    "phone": "+30123456789",
    "discount_tier": "premium"
  }'
```

### Create Order
```bash
curl -X POST https://your-app.ondigitalocean.app/api/orders \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": 1,
    "items": [
      {"product_id": 1, "quantity": 50},
      {"product_id": 2, "quantity": 25}
    ],
    "notes": "Urgent delivery requested"
  }'
```

## Database Schema

### Products Table
- Automatic 10% markup calculation
- Inventory tracking with stock levels
- Brand and category organization
- Supplier information and last update timestamps

### Customers Table  
- Business account information
- Discount tier management
- Contact and delivery details

### Orders Table
- Complete order workflow
- Automatic inventory deduction
- Order status tracking

### Sync Logs Table
- Synchronization history and performance metrics
- Error tracking and debugging information

## Performance Features

- **Batch Processing** - Efficient product synchronization
- **Database Indexing** - Optimized queries for large catalogs
- **Rate Limiting** - Respectful API usage
- **Error Recovery** - Robust sync with automatic retry
- **Background Tasks** - Non-blocking synchronization

## Security

- Environment-based configuration (no credentials in code)
- JWT token handling with automatic refresh
- Input validation and SQL injection prevention
- Error logging without credential exposure

## Monitoring

- Health check endpoints for uptime monitoring
- Comprehensive sync logging
- Performance metrics tracking
- Error reporting and debugging tools

## Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

## License

Private commercial project for EasyCare Market.

## Support

For technical support or business inquiries, contact the development team.

---

**Built for EasyCare Market B2B Operations**
