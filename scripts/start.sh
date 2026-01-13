#!/bin/bash
set -e

echo "Starting InfluencerOps Agent..."

# Check if .env exists
if [ ! -f .env ]; then
    echo "Warning: .env file not found. Creating from .env.example..."
    cp .env.example .env
    echo "Please update .env with your API keys before running again."
    exit 1
fi

# Start Docker services
echo "Starting Docker services..."
docker-compose up -d db redis

# Wait for database to be ready
echo "Waiting for database..."
sleep 5

# Run migrations
echo "Running database migrations..."
docker-compose run --rm api alembic upgrade head

# Start the application
echo "Starting application..."
docker-compose up
