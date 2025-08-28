# EasyCare B2B Platform

A Flask-based B2B wholesale platform with filtered product synchronization.

## Features
- Filtered product sync with customizable parameters
- Real-time inventory management
- RESTful API endpoints
- Performance-optimized database operations

## Environment Variables
Set these in your deployment environment:
- `PANDORA_API_BASE`: Supplier API endpoint
- `PANDORA_USERNAME`: Authentication username
- `PANDORA_PASSWORD`: Authentication password

## Deployment
This app is configured for DigitalOcean App Platform deployment using the included app.yaml specification.

## API Endpoints
- `POST /api/sync-filtered` - Sync products with filters
- `GET /api/stats` - Get database statistics
