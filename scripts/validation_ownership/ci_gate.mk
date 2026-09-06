# Trusted-Make convenience only; CI enters ci_verifier.py directly before Make.
ifneq (,$(filter-out default undefined,$(origin MAKECMDGOALS)))
$(error MAKECMDGOALS must remain owned by GNU Make)
endif

ifneq ($(strip $(MAKECMDGOALS)),validation-ownership-check)
$(error validation-ownership-check must be invoked as the sole Make goal)
endif

override _VALIDATION_OWNERSHIP_FLAGS := \
	$(strip $(MAKEFLAGS) $(MFLAGS) $(GNUMAKEFLAGS))
override _VALIDATION_OWNERSHIP_UNSAFE_FLAGS := \
	$(filter-out j% -j% --jobserver-auth=% --jobserver-fds=% \
		--no-print-directory,$(_VALIDATION_OWNERSHIP_FLAGS))
ifneq ($(_VALIDATION_OWNERSHIP_UNSAFE_FLAGS),)
$(error validation-ownership-check rejects Make execution controls)
endif
ifneq ($(strip $(MAKEOVERRIDES)),)
$(error validation-ownership-check rejects Make variable overrides)
endif
ifeq ($(origin MAKEOVERRIDES),command line)
$(error validation-ownership-check rejects command-line MAKEOVERRIDES)
endif

ifeq ($(strip $(VO_TRUSTED_ROOT)),)
$(error VO_TRUSTED_ROOT is required)
endif
ifeq ($(strip $(VO_REPOSITORY_ROOT)),)
$(error VO_REPOSITORY_ROOT is required)
endif
ifeq ($(strip $(VO_BASE_SHA)),)
$(error VO_BASE_SHA is required)
endif
ifeq ($(strip $(VO_CANDIDATE_SHA)),)
$(error VO_CANDIDATE_SHA is required)
endif

validation-ownership-check:
	@/usr/bin/python3 -I -S -B \
		"$(VO_TRUSTED_ROOT)/scripts/validation_ownership/ci_verifier.py" \
		--trusted-root "$(VO_TRUSTED_ROOT)" \
		--repository-root "$(VO_REPOSITORY_ROOT)" \
		--base-sha "$(VO_BASE_SHA)" \
		--candidate-sha "$(VO_CANDIDATE_SHA)"

.PHONY: validation-ownership-check
