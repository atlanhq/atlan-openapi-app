.PHONY: generate check-generate

generate:
	cd app/contract && pkl eval --multiple-file-output-path generated app.pkl

check-generate: generate
	@git diff --exit-code app/contract/generated/ \
		|| (echo "ERROR: Generated files are stale. Run 'make generate' and commit." && exit 1)
