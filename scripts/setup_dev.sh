#!/bin/bash
set -e

echo "Setting up InfluencerOps development environment..."

# Create virtual environment
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3.11 -m venv .venv
fi

# Activate virtual environment
source .venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install --upgrade pip
pip install -e ".[dev]"

# Create data directories
echo "Creating data directories..."
mkdir -p data/chroma
mkdir -p data/logs

# Copy env file if not exists
if [ ! -f .env ]; then
    echo "Creating .env from .env.example..."
    cp .env.example .env
    echo "Please update .env with your API keys."
fi

# Install pre-commit hooks
echo "Setting up pre-commit hooks..."
pre-commit install

echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo "1. Update .env with your API keys"
echo "2. Start services: docker-compose up -d db redis"
echo "3. Run migrations: alembic upgrade head"
echo "4. Start dev server: uvicorn src.main:app --reload"
