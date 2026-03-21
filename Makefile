.PHONY: install dev dev-backend dev-frontend lint test build

install:
	cd backend && uv sync
	cd frontend && npm install

dev:
	@echo "Starting backend (port 8420) and frontend (port 5173)..."
	@trap 'kill 0' INT TERM; \
		$(MAKE) dev-backend & \
		$(MAKE) dev-frontend & \
		wait

dev-backend:
	cd backend && uv run uvicorn main:app --host 127.0.0.1 --port 8420 --reload

dev-frontend:
	cd frontend && npm run dev

lint:
	cd backend && uv run ruff check .
	cd frontend && npm run lint

test:
	cd backend && uv run pytest
	cd frontend && npm test

build:
	cd frontend && npm run build
