.PHONY: generate check-generate

generate:
	cd contract && pkl eval --multiple-file-output-path output app.pkl
	cp contract/output/input.py src/openapi/_generated_input.py

check-generate: generate
	@git diff --exit-code contract/output/ src/openapi/_generated_input.py \
		|| (echo "ERROR: Generated files are stale. Run 'make generate' and commit." && exit 1)
