.PHONY: generate check-generate test-cloud-integration test-azure-integration

generate:
	pkl eval --project-dir contract -m . contract/app.pkl contract/csa-connectors-objectstore.pkl

check-generate: generate
	@git diff --exit-code app/generated/ atlan.yaml app.yaml \
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
		uv run pytest tests/integration/test_s3_download.py -v -m cloud_integration \
		|| (docker stop minio-test; exit 1)
	docker stop minio-test


test-azure-integration:
	@echo "Starting Azurite..."
	docker run -d --rm --name azurite-test -p 10000:10000 \
		mcr.microsoft.com/azure-storage/azurite:3.35.0 \
		azurite-blob --blobHost 0.0.0.0 --skipApiVersionCheck
	@echo "Waiting for Azurite..." && until curl -s --max-time 2 http://127.0.0.1:10000/devstoreaccount1 > /dev/null 2>&1; do sleep 1; done
	@echo "Creating test container..."
	az storage container create --name test-openapi-specs \
		--connection-string "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
	AZURE_STORAGE_ENDPOINT=http://127.0.0.1:10000 \
		uv run pytest tests/integration/test_azure_download.py -v -m azure_integration \
		|| (docker stop azurite-test; exit 1)
	docker stop azurite-test
