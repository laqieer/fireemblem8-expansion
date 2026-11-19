FE8_PROJECT_PROFILES := reference
FE8_PROJECT_DEFAULT_PROFILE := reference
FE8_PROJECT_REQUIRED_SETTINGS := EXPANSION_SAVE_COMPAT_EPOCH
FE8_PROJECT_reference_EXPANSION_SAVE_COMPAT_EPOCH := 256

FE8_PROJECT_REPLACE := code
FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))
FE8_PROJECT_CODE_REPLACEMENTS := \
	src/bmdebug.c=examples/restoration-of-grado/src/native_bmdebug.c \
	src/bmguide.c=examples/restoration-of-grado/src/static_guide.c \
	src/eventinfo.c=examples/restoration-of-grado/src/native_flags.c \
	src/events_script.c=examples/restoration-of-grado/src/static_guide_events.c \
	src/events_wm.c=examples/restoration-of-grado/src/static_guide_worldmap.c \
	src/savemenu.c=examples/restoration-of-grado/src/native_savemenu.c \
	src/sio_term.c=examples/restoration-of-grado/src/native_sio_term.c
FE8_PROJECT_C_SOURCES := \
	examples/restoration-of-grado/src/state.c \
	examples/restoration-of-grado/src/values.c \
	examples/restoration-of-grado/src/campaign_state.c \
	examples/restoration-of-grado/src/lifecycle.c \
	examples/restoration-of-grado/src/native_save_load.c \
	examples/restoration-of-grado/src/native_save_load_ui.c
