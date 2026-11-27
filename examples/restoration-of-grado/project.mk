FE8_PROJECT_PROFILES := primary
FE8_PROJECT_DEFAULT_PROFILE := primary
FE8_PROJECT_REQUIRED_SETTINGS :=
FE8_PROJECT_REPLACE := assets code
FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))

FE8_PROJECT_SOURCE_assets := examples/restoration-of-grado/assets/manifest.json
FE8_PROJECT_CODE_REPLACEMENTS := \
	src/cg.c=examples/restoration-of-grado/providers/cg.c \
	src/eventscr2.c=examples/restoration-of-grado/providers/eventscr2.c \
	src/data/data_bg.c=examples/restoration-of-grado/providers/data_bg.c \
	src/data/ending/ending_cg.c=examples/restoration-of-grado/providers/ending_cg.c
