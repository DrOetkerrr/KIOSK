PORT ?= 5055

.PHONY: start verify open tail smoke check

start:
	@echo "→ Starting Falkland V2 on http://127.0.0.1:$(PORT)"
	@PORT=$(PORT) bash ./run_falkland.sh

verify:
	@echo "→ Running verify suite against http://127.0.0.1:$(PORT)"
	@PORT=$(PORT) bash ./tools/verify_suite.sh

open:
	@echo "→ Opening http://127.0.0.1:$(PORT)/"
	@{ command -v open >/dev/null 2>&1 && open "http://127.0.0.1:$(PORT)/"; } || true

tail:
	@PORT=$(PORT) $(MAKE) open
	@sleep 1
	@echo "→ Opening http://127.0.0.1:$(PORT)/flight/tail?n=50"
	@{ command -v open >/dev/null 2>&1 && open "http://127.0.0.1:$(PORT)/flight/tail?n=50"; } || true

smoke:
	@echo "→ Running smoke checks against http://127.0.0.1:$(PORT)"
	@PORT=$(PORT) bash ./tools/smoke_check.sh

check:
	@echo "→ Static checks (compile + size guard + route summary)"
	@python3 ./tools/check_repo.py
