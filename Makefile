.PHONY: setup

setup:
	@set -eu; \
	if [ -e .env ]; then \
		printf '%s\n' '.env already exists; keeping it unchanged.'; \
	else \
		secret=$$(openssl rand -hex 32); \
		tmp=$$(mktemp .env.tmp.XXXXXX); \
		trap 'rm -f "$$tmp"' 0; \
		sed "s/^JWT_SECRET_KEY=.*/JWT_SECRET_KEY=$$secret/" .env.example > "$$tmp"; \
		mv "$$tmp" .env; \
		printf '%s\n' 'Created .env with a generated JWT_SECRET_KEY.'; \
	fi

.PHONY: test

test:
	uv run pytest
