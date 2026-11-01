# Shared project declarations; this file also serves the configure/identity query.

PYTHON ?= python3
FE8_PROJECT ?= default
fe8_project_quote = '$(subst ','"'"',$(1))'
fe8_project_name_valid = $(shell $(PYTHON) -c 'import re,sys; print("yes" if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", sys.argv[1]) else "no")' $(call fe8_project_quote,$(1)))
FE8_PROJECT_DATA_MODULES := $(shell $(PYTHON) -c 'from scripts.generated_data import registry; print(" ".join(registry.REGISTRY.all_names()))')
ifeq ($(strip $(FE8_PROJECT_DATA_MODULES)),)
$(error cannot load the generated-data module registry)
endif
FE8_PROJECT_GAME_OPTIONS := mapping width-registry fixed-width-aliases eu-mapping ja-indexed ja-raw zh-indexed zh-raw latin-span-review unicode-script-review raw-closure
FE8_PROJECT_GAME_CJK_LOCALES := ja zh-Hans
FE8_PROJECT_GAME_EU_LOCALES := fr de es it
FE8_PROJECT_GAME_MODULES := $(addprefix game-,$(FE8_PROJECT_GAME_OPTIONS)) \
	$(addprefix game-authored-,$(FE8_PROJECT_GAME_CJK_LOCALES) $(FE8_PROJECT_GAME_EU_LOCALES)) \
	$(addprefix game-indexed-,$(FE8_PROJECT_GAME_EU_LOCALES))
FE8_PROJECT_FILE_MODULES := $(FE8_PROJECT_DATA_MODULES) assets $(FE8_PROJECT_GAME_MODULES)
FE8_PROJECT_MODULES := $(FE8_PROJECT_FILE_MODULES) code game-text ui-text fonts

ifneq ($(call fe8_project_name_valid,$(FE8_PROJECT)),yes)
$(error invalid FE8_PROJECT; expected an example directory name or default)
endif

ifeq ($(FE8_PROJECT),default)
FE8_PROJECT_PROFILES := default
FE8_PROJECT_DEFAULT_PROFILE := default
FE8_PROJECT_REQUIRED_SETTINGS :=
FE8_PROJECT_INHERIT := $(FE8_PROJECT_MODULES)
FE8_PROJECT_REPLACE :=
else
FE8_PROJECT_DIR := examples/$(FE8_PROJECT)
FE8_PROJECT_DECLARATION := $(FE8_PROJECT_DIR)/project.mk
ifeq ($(wildcard $(FE8_PROJECT_DECLARATION)),)
$(error unknown FE8_PROJECT '$(FE8_PROJECT)': missing $(FE8_PROJECT_DECLARATION))
endif
include $(FE8_PROJECT_DECLARATION)
endif

FE8_PROJECT_PROFILE ?= $(FE8_PROJECT_DEFAULT_PROFILE)
ifneq ($(call fe8_project_name_valid,$(FE8_PROJECT_PROFILE)),yes)
$(error invalid FE8_PROJECT_PROFILE for project '$(FE8_PROJECT)')
endif
ifeq ($(filter $(FE8_PROJECT_PROFILE),$(FE8_PROJECT_PROFILES)),)
$(error unsupported FE8_PROJECT_PROFILE '$(FE8_PROJECT_PROFILE)' for project '$(FE8_PROJECT)')
endif

FE8_PROJECT_SUPPORTED_SETTINGS := MODERN_ROM_SIZE EXPANSION_ROM_TITLE \
	EXPANSION_ENABLED_LOCALES EXPANSION_DEFAULT_LOCALE EXPANSION_SAVE_COMPAT_EPOCH
ifneq ($(filter-out $(FE8_PROJECT_SUPPORTED_SETTINGS),$(FE8_PROJECT_REQUIRED_SETTINGS)),)
$(error unsupported project setting: $(filter-out $(FE8_PROJECT_SUPPORTED_SETTINGS),$(FE8_PROJECT_REQUIRED_SETTINGS)))
endif

define fe8_apply_project_default
ifeq ($$(strip $$(FE8_PROJECT_$$(FE8_PROJECT_PROFILE)_$(1))),)
$$(error missing $(1) in project '$$(FE8_PROJECT)' profile '$$(FE8_PROJECT_PROFILE)')
endif
ifeq ($$(origin $(1)),undefined)
$(1) := $$(FE8_PROJECT_$$(FE8_PROJECT_PROFILE)_$(1))
endif
endef
$(foreach setting,$(FE8_PROJECT_REQUIRED_SETTINGS),$(eval $(call fe8_apply_project_default,$(setting))))

ifneq ($(filter-out $(FE8_PROJECT_MODULES),$(FE8_PROJECT_INHERIT) $(FE8_PROJECT_REPLACE)),)
$(error unknown project input module: $(filter-out $(FE8_PROJECT_MODULES),$(FE8_PROJECT_INHERIT) $(FE8_PROJECT_REPLACE)))
endif
ifneq ($(filter $(FE8_PROJECT_INHERIT),$(FE8_PROJECT_REPLACE)),)
$(error project input module has both inherit and replace providers)
endif
ifneq ($(words $(FE8_PROJECT_INHERIT) $(FE8_PROJECT_REPLACE)),$(words $(sort $(FE8_PROJECT_INHERIT) $(FE8_PROJECT_REPLACE))))
$(error duplicate project input provider)
endif
ifneq ($(filter-out $(FE8_PROJECT_INHERIT) $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES)),)
$(error missing project input provider: $(filter-out $(FE8_PROJECT_INHERIT) $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES)))
endif

FE8_PROJECT_DECLARED_SOURCES := $(patsubst FE8_PROJECT_SOURCE_%,%,$(filter FE8_PROJECT_SOURCE_%,$(.VARIABLES)))
ifneq ($(filter-out $(FE8_PROJECT_FILE_MODULES),$(FE8_PROJECT_DECLARED_SOURCES)),)
$(error unknown project source: $(filter-out $(FE8_PROJECT_FILE_MODULES),$(FE8_PROJECT_DECLARED_SOURCES)))
endif
ifneq ($(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_DECLARED_SOURCES)),)
$(error inherited modules cannot declare replacement sources: $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_DECLARED_SOURCES)))
endif

FE8_PROJECT_REPLACED_DATA := $(filter $(FE8_PROJECT_DATA_MODULES),$(FE8_PROJECT_REPLACE))
define fe8_require_project_source
ifneq ($$(words $$(FE8_PROJECT_SOURCE_$(1))),1)
$$(error project replacement '$(1)' requires one FE8_PROJECT_SOURCE_$(1) path)
endif
ifeq ($$(wildcard $$(FE8_PROJECT_SOURCE_$(1))),)
$$(error missing project replacement '$(1)': $$(FE8_PROJECT_SOURCE_$(1)))
endif
endef
$(foreach module,$(filter $(FE8_PROJECT_FILE_MODULES),$(FE8_PROJECT_REPLACE)),$(eval $(call fe8_require_project_source,$(module))))

fe8_project_pair_key = $(firstword $(subst =, ,$(1)))
fe8_project_pair_value = $(word 2,$(subst =, ,$(1)))
FE8_PROJECT_FONT_SLOTS := src/fontgrp-data.c src/data/localized_font_data.c
ifneq ($(filter-out $(FE8_PROJECT_FONT_SLOTS),$(foreach pair,$(FE8_PROJECT_FONT_REPLACEMENTS),$(call fe8_project_pair_key,$(pair)))),)
$(error fonts may replace only $(FE8_PROJECT_FONT_SLOTS))
endif
ifneq ($(filter $(FE8_PROJECT_FONT_SLOTS),$(foreach pair,$(FE8_PROJECT_CODE_REPLACEMENTS),$(call fe8_project_pair_key,$(pair)))),)
$(error font source replacements belong to the fonts provider)
endif
ifneq ($(strip $(FE8_PROJECT_CODE_REPLACEMENTS) $(FE8_PROJECT_C_SOURCES)),)
ifeq ($(filter code,$(FE8_PROJECT_REPLACE)),)
$(error code inputs require the replace provider for code)
endif
endif
ifneq ($(strip $(FE8_PROJECT_FONT_REPLACEMENTS)),)
ifeq ($(filter fonts,$(FE8_PROJECT_REPLACE)),)
$(error font inputs require the replace provider for fonts)
endif
endif
ifneq ($(filter code,$(FE8_PROJECT_REPLACE)),)
ifeq ($(strip $(FE8_PROJECT_CODE_REPLACEMENTS) $(FE8_PROJECT_C_SOURCES)),)
$(error the code replacement provider requires replacements or additional C sources)
endif
endif
ifneq ($(filter fonts,$(FE8_PROJECT_REPLACE)),)
ifeq ($(strip $(FE8_PROJECT_FONT_REPLACEMENTS)),)
$(error the fonts replacement provider requires FE8_PROJECT_FONT_REPLACEMENTS)
endif
endif
FE8_PROJECT_C_REPLACEMENTS := $(FE8_PROJECT_CODE_REPLACEMENTS) $(FE8_PROJECT_FONT_REPLACEMENTS)
FE8_PROJECT_C_SLOTS := $(foreach pair,$(FE8_PROJECT_C_REPLACEMENTS),$(call fe8_project_pair_key,$(pair)))
ifneq ($(filter src/msg_data.c,$(FE8_PROJECT_C_SLOTS)),)
$(error src/msg_data.c belongs to the game-text provider)
endif
ifneq ($(words $(FE8_PROJECT_C_SLOTS)),$(words $(sort $(FE8_PROJECT_C_SLOTS))))
$(error more than one replacement for a C source)
endif
define fe8_require_project_c_replacement
ifneq ($$(words $$(subst =, ,$(1))),2)
$$(error invalid C replacement '$(1)'; expected SOURCE=PROJECT_SOURCE)
endif
ifneq ($$(filter src/%.c,$$(call fe8_project_pair_key,$(1))),$$(call fe8_project_pair_key,$(1)))
$$(error invalid logical C source in '$(1)')
endif
ifeq ($$(wildcard $$(call fe8_project_pair_key,$(1))),)
$$(error unknown logical C source in '$(1)')
endif
ifneq ($$(filter $$(FE8_PROJECT_DIR)/%.c,$$(call fe8_project_pair_value,$(1))),$$(call fe8_project_pair_value,$(1)))
$$(error replacement C source must belong to $$(FE8_PROJECT_DIR): '$(1)')
endif
ifeq ($$(wildcard $$(call fe8_project_pair_value,$(1))),)
$$(error missing replacement C source in '$(1)')
endif
endef
$(foreach pair,$(FE8_PROJECT_C_REPLACEMENTS),$(eval $(call fe8_require_project_c_replacement,$(pair))))
define fe8_require_project_c_source
ifneq ($$(filter $$(FE8_PROJECT_DIR)/%.c,$(1)),$(1))
$$(error additional C source must belong to $$(FE8_PROJECT_DIR): '$(1)')
endif
ifeq ($$(wildcard $(1)),)
$$(error missing additional C source '$(1)')
endif
endef
$(foreach source,$(FE8_PROJECT_C_SOURCES),$(eval $(call fe8_require_project_c_source,$(source))))

define fe8_require_project_file
ifneq ($$(words $$($(1))),1)
$$(error project replacement requires one $(1) path)
endif
ifeq ($$(wildcard $$($(1))),)
$$(error missing project input $(1): $$($(1)))
endif
endef
ifneq ($(filter game-text,$(FE8_PROJECT_REPLACE)),)
$(foreach variable,FE8_PROJECT_TEXT_MAIN FE8_PROJECT_TEXT_DEFS,$(eval $(call fe8_require_project_file,$(variable))))
else
ifneq ($(strip $(FE8_PROJECT_TEXT_MAIN) $(FE8_PROJECT_TEXT_DEFS)),)
$(error text inputs require the replace provider for game-text)
endif
endif

define fe8_require_project_catalog
ifneq ($$(words $$(subst =, ,$(1))),2)
$$(error invalid catalog '$(1)'; expected LOCALE=PATH)
endif
ifeq ($$(wildcard $$(call fe8_project_pair_value,$(1))),)
$$(error missing project catalog in '$(1)')
endif
endef
ifneq ($(filter ui-text,$(FE8_PROJECT_REPLACE)),)
$(eval $(call fe8_require_project_file,FE8_PROJECT_UI_REGISTRY))
ifeq ($(strip $(FE8_PROJECT_UI_CATALOGS)),)
$(error ui-text replacement requires FE8_PROJECT_UI_CATALOGS)
endif
$(foreach pair,$(FE8_PROJECT_UI_CATALOGS),$(eval $(call fe8_require_project_catalog,$(pair))))
FE8_PROJECT_UI_INPUTS := $(FE8_PROJECT_UI_REGISTRY) $(foreach pair,$(FE8_PROJECT_UI_CATALOGS),$(call fe8_project_pair_value,$(pair)))
FE8_PROJECT_UI_ARGS := --registry $(call fe8_project_quote,$(FE8_PROJECT_UI_REGISTRY)) $(foreach pair,$(FE8_PROJECT_UI_CATALOGS),--catalog $(call fe8_project_quote,$(pair)))
FE8_PROJECT_UI_DATA_ARGS := --ui-registry $(call fe8_project_quote,$(FE8_PROJECT_UI_REGISTRY)) $(foreach pair,$(FE8_PROJECT_UI_CATALOGS),--ui-catalog $(call fe8_project_quote,$(pair)))
else
ifneq ($(strip $(FE8_PROJECT_UI_REGISTRY) $(FE8_PROJECT_UI_CATALOGS)),)
$(error UI catalog inputs require the replace provider for ui-text)
endif
endif

ifneq ($(filter assets,$(FE8_PROJECT_REPLACE)),)
$(eval $(call fe8_require_project_file,FE8_PROJECT_SOURCE_assets))
ifneq ($(origin ASSET_MANIFEST),undefined)
ifneq ($(abspath $(ASSET_MANIFEST)),$(abspath $(FE8_PROJECT_SOURCE_assets)))
$(error ASSET_MANIFEST contradicts the selected assets provider)
endif
endif
ASSET_MANIFEST := $(FE8_PROJECT_SOURCE_assets)
else
ifneq ($(strip $(FE8_PROJECT_SOURCE_assets)),)
$(error asset input requires the replace provider for assets)
endif
endif

fe8_project_source = $(if $(filter $(1),$(FE8_PROJECT_REPLACE)),$(FE8_PROJECT_SOURCE_$(1)),$(2))
fe8_project_c_source = $(or $(call fe8_project_pair_value,$(filter $(1)=%,$(FE8_PROJECT_C_REPLACEMENTS))),$(1))
FE8_PROJECT_DATA_INPUTS := $(foreach module,$(FE8_PROJECT_REPLACED_DATA),$(FE8_PROJECT_SOURCE_$(module)))
FE8_PROJECT_GAME_INPUTS := $(FE8_PROJECT_TEXT_MAIN) $(FE8_PROJECT_TEXT_DEFS) \
	$(foreach module,$(filter $(FE8_PROJECT_GAME_MODULES),$(FE8_PROJECT_REPLACE)),$(FE8_PROJECT_SOURCE_$(module))) \
	$(FE8_PROJECT_UI_INPUTS)
FE8_PROJECT_SCHEMA_ARGS = $(foreach module,$(FE8_PROJECT_REPLACED_DATA),--source-override $(call fe8_project_quote,$(module)=$(FE8_PROJECT_SOURCE_$(module)))) $(if $(MODERN_PROJECT_MSG_HEADER),--message-header $(call fe8_project_quote,$(MODERN_PROJECT_MSG_HEADER))) $(if $(filter-out default,$(FE8_PROJECT)),--asset-manifest $(call fe8_project_quote,$(or $(ASSET_MANIFEST),assets/manifest.json)))
FE8_PROJECT_DATA_ARGS = $(FE8_PROJECT_SCHEMA_ARGS) $(if $(filter-out default,$(FE8_PROJECT)),--inventory-dir $(call fe8_project_quote,$(GENERATED_DATA_OUT_DIR)/reports)) $(FE8_PROJECT_UI_DATA_ARGS)
FE8_PROJECT_GAME_ARGS = \
	$(foreach option,$(FE8_PROJECT_GAME_OPTIONS),$(if $(filter game-$(option),$(FE8_PROJECT_REPLACE)),--$(option) $(call fe8_project_quote,$(FE8_PROJECT_SOURCE_game-$(option))))) \
	$(if $(filter $(addprefix game-authored-,$(FE8_PROJECT_GAME_CJK_LOCALES)),$(FE8_PROJECT_REPLACE)),$(foreach locale,$(FE8_PROJECT_GAME_CJK_LOCALES),--authored $(call fe8_project_quote,$(locale)=$(call fe8_project_source,game-authored-$(locale),texts/locales/authored/catalog.$(locale).json)))) \
	$(if $(filter $(addprefix game-authored-,$(FE8_PROJECT_GAME_EU_LOCALES)),$(FE8_PROJECT_REPLACE)),$(foreach locale,$(FE8_PROJECT_GAME_EU_LOCALES),--eu-authored $(call fe8_project_quote,$(locale)=$(call fe8_project_source,game-authored-$(locale),texts/locales/eu/authored.$(locale).json)))) \
	$(if $(filter $(addprefix game-indexed-,$(FE8_PROJECT_GAME_EU_LOCALES)),$(FE8_PROJECT_REPLACE)),$(foreach locale,$(FE8_PROJECT_GAME_EU_LOCALES),--eu-indexed $(call fe8_project_quote,$(locale)=$(call fe8_project_source,game-indexed-$(locale),texts/locales/eu/indexed.$(locale).txt)))) \
	$(if $(MODERN_PROJECT_MSG_HEADER),--english-texts $(call fe8_project_quote,$(FE8_PROJECT_TEXT_MAIN)) --english-definitions $(call fe8_project_quote,$(FE8_PROJECT_TEXT_DEFS)) --target-header $(call fe8_project_quote,$(MODERN_PROJECT_MSG_HEADER))) \
	$(foreach pair,$(FE8_PROJECT_UI_CATALOGS),--expansion-catalog $(call fe8_project_quote,$(pair)))

ifneq ($(FE8_PROJECT),default)
ifneq ($(origin MODERN_BUILD_ROOT),undefined)
ifeq ($(strip $(MODERN_BUILD_ROOT)),)
$(error MODERN_BUILD_ROOT must be a nonempty outer build directory)
endif
endif
ifneq ($(or $(MODERN_ABI),aapcs),aapcs)
$(error FE8_PROJECT=$(FE8_PROJECT) supports only modern AAPCS)
endif
FE8_PROJECT_ARCHIVAL_C_GOALS := $(strip $(foreach goal,$(MAKECMDGOALS),$(if $(and $(filter %.o %.s,$(goal)),$(wildcard $(basename $(goal)).c)),$(goal))))
ifneq ($(filter legacy legacy-% fireemblem8% objects.lst shiftcheck% build/shiftcheck/% build/legacy/% src/%.c banim/data_banim.o%,$(MAKECMDGOALS))$(FE8_PROJECT_ARCHIVAL_C_GOALS),)
$(error FE8_PROJECT=$(FE8_PROJECT) is unsupported by the archival lane, including mixed requests)
endif
define fe8_reject_project_output_override
ifneq ($$(origin $(1)),undefined)
$$(error $(1) is derived for selected projects; set MODERN_BUILD_ROOT instead)
endif
endef
FE8_PROJECT_PATH_INPUTS := MODERN_BUILD_ROOT MODERN_TOOLCHAIN_ROOT \
	MODERN_BINUTILS_DIR MODERN_LIBGCC_DIR MODERN_LIBC_DIR
FE8_PROJECT_DERIVED_DIRS := $(filter-out $(FE8_PROJECT_PATH_INPUTS),$(filter MODERN_%_ROOT MODERN_%_DIR ASSET_%_ROOT ASSET_%_DIR,$(.VARIABLES)))
$(foreach variable,$(sort FE8_PROJECT_OUTPUT_DIR MODERN_SELECTED_BUILD_ROOT MODERN_OUTPUT_DIR GENERATED_DATA_OUT_DIR LOCALIZATION_OUT_DIR GAME_LOCALIZATION_OUT_DIR ASSET_OUTPUT_DIR BANIM_OBJECT $(FE8_PROJECT_DERIVED_DIRS)),$(eval $(call fe8_reject_project_output_override,$(variable))))
endif

fe8_project_build_root = $(if $(filter-out default,$(FE8_PROJECT)),$(1)/projects/$(FE8_PROJECT)/$(FE8_PROJECT_PROFILE),$(1))
fe8_project_output_dir = $(call fe8_project_build_root,$(1))/$(or $(2),debug)/$(or $(3),aapcs)
fe8_project_aux_root = $(if $(filter-out default,$(FE8_PROJECT)),$(or $(MODERN_BUILD_ROOT),build)/profiles/$(1),$(2))
fe8_project_aux_output_dir = $(if $(filter-out default,$(FE8_PROJECT)),$(call fe8_project_output_dir,$(1),$(2),$(3)),$(1))
FE8_PROJECT_OUTPUT_DIR = $(call fe8_project_output_dir,$(or $(MODERN_BUILD_ROOT),build),$(MODERN_CONFIG),$(MODERN_ABI))

fe8_project_settings_env = $(foreach setting,$(FE8_PROJECT_REQUIRED_SETTINGS),FE8_PROJECT_SETTING_$(setting)=$(call fe8_project_quote,$(FE8_PROJECT_$(FE8_PROJECT_PROFILE)_$(setting))))

.PHONY: project-selection-config
project-selection-config:
	@FE8_PROJECT=$(call fe8_project_quote,$(FE8_PROJECT)) FE8_PROJECT_PROFILE=$(call fe8_project_quote,$(FE8_PROJECT_PROFILE)) FE8_PROJECT_REQUIRED_SETTINGS=$(call fe8_project_quote,$(FE8_PROJECT_REQUIRED_SETTINGS)) FE8_PROJECT_ASSET_MANIFEST=$(call fe8_project_quote,$(if $(filter assets,$(FE8_PROJECT_REPLACE)),$(FE8_PROJECT_SOURCE_assets))) $(fe8_project_settings_env) $(PYTHON) -c 'import json,os; keys=os.environ["FE8_PROJECT_REQUIRED_SETTINGS"].split(); print(json.dumps({"project":os.environ["FE8_PROJECT"],"profile":os.environ["FE8_PROJECT_PROFILE"],"settings":{key:os.environ["FE8_PROJECT_SETTING_"+key] for key in keys},"asset_manifest":os.environ["FE8_PROJECT_ASSET_MANIFEST"] or None},sort_keys=True))'

.PHONY: project-selection-archival-guard
project-selection-archival-guard:
	@$(if $(filter-out default,$(FE8_PROJECT)),$(error FE8_PROJECT=$(FE8_PROJECT) is unsupported by the archival lane),:)
