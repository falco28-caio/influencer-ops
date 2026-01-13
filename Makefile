.PHONY: setup dev test lint format migrate docker-up docker-down clean

# Development setup
setup:
	@chmod +x scripts/*.sh
	@./scripts/setup_dev.sh

# Start development server
dev:
	uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# Run tests
test:
	pytest tests/ -v --cov=src --cov-report=term-missing

# Run linter
lint:
	ruff check src/ tests/
	mypy src/

# Format code
format:
	black src/ tests/
	ruff check src/ tests/ --fix

# Run database migrations
migrate:
	alembic upgrade head

# Create new migration
migration:
	@read -p "Migration message: " msg; \
	alembic revision --autogenerate -m "$$msg"

# Start Docker services
docker-up:
	docker-compose up -d

# Stop Docker services
docker-down:
	docker-compose down

# Build Docker images
docker-build:
	docker-compose build

# Start everything (database, redis, api, worker)
start:
	@chmod +x scripts/start.sh
	@./scripts/start.sh

# Start Celery worker
worker:
	celery -A src.workers.celery_app worker --loglevel=info

# Start Celery beat (scheduler)
beat:
	celery -A src.workers.celery_app beat --loglevel=info

# Sync Notion SOPs
sync-sops:
	python -c "from src.workers.tasks import sync_notion_task; sync_notion_task()"

# Sync HubSpot contacts
sync-hubspot:
	python -c "from src.workers.tasks import sync_hubspot_task; sync_hubspot_task()"

# Clean up
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +

# Show help
help:
	@echo "Available commands:"
	@echo "  make setup        - Set up development environment"
	@echo "  make dev          - Start development server"
	@echo "  make test         - Run tests"
	@echo "  make lint         - Run linter"
	@echo "  make format       - Format code"
	@echo "  make migrate      - Run database migrations"
	@echo "  make migration    - Create new migration"
	@echo "  make docker-up    - Start Docker services"
	@echo "  make docker-down  - Stop Docker services"
	@echo "  make start        - Start everything"
	@echo "  make worker       - Start Celery worker"
	@echo "  make beat         - Start Celery beat scheduler"
	@echo "  make sync-sops    - Sync SOPs from Notion"
	@echo "  make sync-hubspot - Sync contacts from HubSpot"
	@echo "  make clean        - Clean up cache files"
