# Standalone read-only technical release checks.
#
# `release-check` returns success only when concrete candidate-tree,
# configuration, source-guard, submodule, migration, and documentation checks
# pass. `release-rehearse` additionally verifies a deterministic source
# archive. Neither target has publishing side effects.
#
# `release-candidate-tree-check` validates
# paths, modes, and gitlinks from the exact Git tree; no committed membership
# ledger participates.

.PHONY: release-test release-migrations-check release-rehearse release-check \
        release-changelog-check release-candidate-tree-check \
        release-submodule-binding-check release-epoch-claims-check \
        release-stale-count-claims-check

# RELEASE_TARGET_SHA -- issue #9 mandatory correction (exact target-SHA
# binding): every local archive command below passes
# this explicitly as `--target-sha`, rather than letting
# scripts/release_rehearsal/manifest.py's own `resolve_target_sha`
# implicitly fall back to a bare `git rev-parse HEAD` call. Locally, this
# still resolves to the exact current HEAD by default (identical
# behavior to before) -- but it is now an explicit, auditable value any
# caller can see/override. It defaults to the exact local HEAD commit so
# local archive commands never resolve a second, ambiguous revision.
RELEASE_TARGET_SHA ?= $(shell git rev-parse HEAD)

release-test:
	$(PYTHON) -m unittest discover -s scripts/release_rehearsal/tests -v
	$(PYTHON) -m unittest discover -s scripts/modernize/migrations/tests -v

release-migrations-check:
	$(PYTHON) -m scripts.modernize.migrations.cli check

release-changelog-check:
	$(PYTHON) -m scripts.release_rehearsal.changelog check

release-rehearse:
	$(PYTHON) -m scripts.release_rehearsal.cli rehearse --target-sha $(RELEASE_TARGET_SHA)

release-check:
	$(PYTHON) -m scripts.release_rehearsal.cli check --target-sha $(RELEASE_TARGET_SHA)

release-candidate-tree-check:
	$(PYTHON) -m scripts.release_rehearsal.cli candidate-tree --target-sha $(RELEASE_TARGET_SHA)

release-submodule-binding-check:
	$(PYTHON) -m scripts.release_rehearsal.submodule_binding

release-epoch-claims-check:
	$(PYTHON) -m scripts.release_rehearsal.epoch_claims

release-stale-count-claims-check:
	$(PYTHON) -m scripts.release_rehearsal.stale_count_claims
