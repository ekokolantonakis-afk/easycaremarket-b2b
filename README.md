# EasyCare Market B2B Platform

B2B wholesale platform with  integration for EasyCare Market.

## Features
- Product catalog with search and filtering
- API integration
- Real-time inventory sync
- B2B pricing with markup

## Deployment
This app is designed for DigitalOcean App Platform deployment.

## Environment Variables Required
- `QOGITA_API_KEY`: Your Qogita API key
- `QOGITA_BASE_URL`: Qogita API base URL

## API Endpoints
- `/health` - Health check
- `/api/test` - API functionality test  
- `/api/qogita-test` - Test Qogita connection
- `/api/products` - Get products with pagination
- `/api/sync-status` - Check sync status
