.PHONY: generate check-generate test-cloud-integration

generate:
	pkl eval --project-dir contract -m app/generated contract/app.pkl contract/csa-connectors-objectstore.pkl

check-generate: generate
	@git diff --exit-code app/generated/ \
		|| (echo "ERROR: Generated files are stale. Run 'make generate' and commit." && exit 1)

test-cloud-integration:
	@echo "Starting MinIO..."
	docker run -d --rm --name minio-test -p 9000:9000 \
		-e MINIO_ROOT_USER=minioadmin \
		-e MINIO_ROOT_PASSWORD=minioadmin \
		minio/minio server /data
	@echo "Waiting for MinIO..." && until curl -sf http://localhost:9000/minio/health/live; do sleep 1; done
	@echo "Creating test bucket..."
	AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin \
		aws --endpoint-url http://localhost:9000 \
		s3api create-bucket --bucket test-openapi-specs --region us-east-1
	AWS_ENDPOINT_URL=http://localhost:9000 \
		uv run pytest tests/integration/test_cloud_download.py -v -m cloud_integration \
		|| (docker stop minio-test; exit 1)
	docker stop minio-test
