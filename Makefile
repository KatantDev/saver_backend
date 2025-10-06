.PHONY: deploy migrate locales

deploy:
	docker compose build
	docker rollout api
	docker compose exec nginx nginx -s reload
	docker rollout taskiq-worker
	docker rollout taskiq-scheduler

migrate:
	docker compose build
	docker compose up migrator

build-local:
	docker compose -f docker-compose.yml -f deploy/docker-compose.dev.yml --project-directory . build

deploy-local:
	docker compose -f docker-compose.yml -f deploy/docker-compose.dev.yml --project-directory . up --build -d

locales:
	pybabel extract --input-dirs=. -o locales/messages.pot --project=Sounds --version=0.0.1 -k __
	pybabel update -d locales -D messages -i locales/messages.pot --no-wrap -N
