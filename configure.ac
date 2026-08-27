AC_PREREQ([2.69])
AC_INIT(
    [fireemblem8-expansion],
    [0.1.0],
    [https://github.com/laqieer/fireemblem8-expansion/issues])
AC_CONFIG_SRCDIR([config.mk])

AC_PATH_PROGS([PYTHON], [python3 python])
AS_IF([test -z "$PYTHON"], [AC_MSG_ERROR([Python 3 is required])])

AC_MSG_CHECKING([whether $PYTHON is Python 3])
if "$PYTHON" -c 'import sys; raise SystemExit(sys.version_info.major != 3)'
then
    AC_MSG_RESULT([yes])
else
    AC_MSG_RESULT([no])
    AC_MSG_ERROR([Python 3 is required])
fi

AC_ARG_ENABLE(
    [mechanics-hooks],
    [AS_HELP_STRING(
        [--enable-mechanics-hooks],
        [link the public battle-stat mechanics hook registry])],
    [AS_CASE(
        [$enableval],
        [yes], [fe8_mechanics_hooks=1],
        [no], [fe8_mechanics_hooks=0],
        [AC_MSG_ERROR([--enable-mechanics-hooks accepts only yes or no])])],
    [fe8_mechanics_hooks=])

AC_ARG_ENABLE(
    [mechanics-sample],
    [AS_HELP_STRING(
        [--enable-mechanics-sample],
        [register the bundled sample mechanic (requires mechanics hooks)])],
    [AS_CASE(
        [$enableval],
        [yes], [fe8_mechanics_sample=1],
        [no], [fe8_mechanics_sample=0],
        [AC_MSG_ERROR([--enable-mechanics-sample accepts only yes or no])])],
    [fe8_mechanics_sample=])

AC_ARG_ENABLE(
    [danger-overlay-menu],
    [AS_HELP_STRING(
        [--enable-danger-overlay-menu],
        [expose the player Threat Range map-menu entry])],
    [AS_CASE(
        [$enableval],
        [yes], [fe8_danger_overlay_menu=1],
        [no], [fe8_danger_overlay_menu=0],
        [AC_MSG_ERROR([--enable-danger-overlay-menu accepts only yes or no])])],
    [fe8_danger_overlay_menu=])

AC_ARG_ENABLE(
    [blue-phase-delegate],
    [AS_HELP_STRING(
        [--enable-blue-phase-delegate],
        [add one-phase Charge delegation to the map menu])],
    [AS_CASE(
        [$enableval],
        [yes], [fe8_blue_phase_delegate=1],
        [no], [fe8_blue_phase_delegate=0],
        [AC_MSG_ERROR([--enable-blue-phase-delegate accepts only yes or no])])],
    [fe8_blue_phase_delegate=])

AC_ARG_ENABLE(
    [starter-content],
    [AS_HELP_STRING(
        [--enable-starter-content],
        [link the generated starter item and mechanic])],
    [AS_CASE(
        [$enableval],
        [yes], [fe8_starter_content=1],
        [no], [fe8_starter_content=0],
        [AC_MSG_ERROR([--enable-starter-content accepts only yes or no])])],
    [fe8_starter_content=])

AC_ARG_ENABLE(
    [aoe-reference],
    [AS_HELP_STRING(
        [--enable-aoe-reference],
        [enable the bounded typed-AoE reference probe/effect])],
    [AS_CASE(
        [$enableval],
        [yes], [fe8_aoe_reference=1],
        [no], [fe8_aoe_reference=0],
        [AC_MSG_ERROR([--enable-aoe-reference accepts only yes or no])])],
    [fe8_aoe_reference=])

AC_ARG_ENABLE(
    [custom-spell-effects],
    [AS_HELP_STRING(
        [--enable-custom-spell-effects],
        [enable the bounded typed custom battle spell-effect runtime])],
    [AS_CASE(
        [$enableval],
        [yes], [fe8_custom_spell_effects=1],
        [no], [fe8_custom_spell_effects=0],
        [AC_MSG_ERROR([--enable-custom-spell-effects accepts only yes or no])])],
    [fe8_custom_spell_effects=])

AC_ARG_WITH(
    [asset-manifest],
    [AS_HELP_STRING(
        [--with-asset-manifest=PATH],
        [persist the source-owned asset manifest selected by ordinary make])],
    [AS_CASE(
        [$withval],
        [yes|no], [AC_MSG_ERROR([--with-asset-manifest requires a path])],
        [fe8_asset_manifest=$withval])],
    [fe8_asset_manifest=])

AC_ARG_ENABLE(
    [localized-text-auto-wrap],
    [AS_HELP_STRING(
        [--enable-localized-text-auto-wrap],
        [enable the runtime localized-text wrapping safety net])],
    [AS_CASE(
        [$enableval],
        [yes], [fe8_localized_text_auto_wrap=1],
        [no], [fe8_localized_text_auto_wrap=0],
        [AC_MSG_ERROR([--enable-localized-text-auto-wrap accepts only yes or no])])],
    [fe8_localized_text_auto_wrap=])

AC_ARG_ENABLE(
    [casual-mode],
    [AS_HELP_STRING(
        [--enable-casual-mode],
        [restore eligible ordinary player defeats at chapter boundaries])],
    [AS_CASE(
        [$enableval],
        [yes], [fe8_casual_mode=1],
        [no], [fe8_casual_mode=0],
        [AC_MSG_ERROR([--enable-casual-mode accepts only yes or no])])],
    [fe8_casual_mode=])

AC_ARG_ENABLE(
    [hq-mixer],
    [AS_HELP_STRING(
        [--enable-hq-mixer],
        [select the optional high-resolution MP2K PCM mixer (modern AAPCS only)])],
    [AS_CASE(
        [$enableval],
        [yes], [fe8_hq_mixer=1],
        [no], [fe8_hq_mixer=0],
        [AC_MSG_ERROR([--enable-hq-mixer accepts only yes or no])])],
    [fe8_hq_mixer=])

AC_ARG_ENABLE(
    [autoplay-strategies],
    [AS_HELP_STRING(
        [--enable-autoplay-strategies],
        [enable Aggressive and Objective-first autoplay strategy references])],
    [AS_CASE(
        [$enableval],
        [yes], [fe8_autoplay_strategies=1],
        [no], [fe8_autoplay_strategies=0],
        [AC_MSG_ERROR([--enable-autoplay-strategies accepts only yes or no])])],
    [fe8_autoplay_strategies=])

AC_ARG_WITH(
    [bgm-continuation-policy],
    [AS_HELP_STRING(
        [--with-bgm-continuation-policy=preserve|resume|restart],
        [music continuation policy across map, battle, and menu transitions])],
    [AS_CASE(
        [$withval],
        [preserve], [fe8_bgm_continuation_policy=preserve],
        [resume], [fe8_bgm_continuation_policy=resume],
        [restart], [fe8_bgm_continuation_policy=restart],
        [AC_MSG_ERROR([--with-bgm-continuation-policy accepts only preserve, resume, or restart])])],
    [fe8_bgm_continuation_policy=])

AC_ARG_ENABLE(
    [pseudo-locale],
    [AS_HELP_STRING(
        [--enable-pseudo-locale],
        [enable qps-ploc (also include it in --with-enabled-locales)])],
    [AS_CASE(
        [$enableval],
        [yes], [fe8_pseudo_locale=1],
        [no], [fe8_pseudo_locale=0],
        [AC_MSG_ERROR([--enable-pseudo-locale accepts only yes or no])])],
    [fe8_pseudo_locale=])

AC_ARG_WITH(
    [enabled-locales],
    [AS_HELP_STRING(
        [--with-enabled-locales=LIST],
        [comma-separated locale IDs, including en])],
    [AS_CASE(
        [$withval],
        [yes|no], [AC_MSG_ERROR([--with-enabled-locales requires a value])],
        [fe8_enabled_locales=$withval])],
    [fe8_enabled_locales=])

AC_ARG_WITH(
    [default-locale],
    [AS_HELP_STRING(
        [--with-default-locale=ID],
        [default locale ID; must be in --with-enabled-locales])],
    [AS_CASE(
        [$withval],
        [yes|no], [AC_MSG_ERROR([--with-default-locale requires a value])],
        [fe8_default_locale=$withval])],
    [fe8_default_locale=])

AC_ARG_WITH(
    [rom-size],
    [AS_HELP_STRING(
        [--with-rom-size=16M|32M],
        [modern ROM size; CJK locale profiles require 32M])],
    [AS_CASE(
        [$withval],
        [yes|no], [AC_MSG_ERROR([--with-rom-size requires 16M or 32M])],
        [fe8_rom_size=$withval])],
    [fe8_rom_size=])

AC_ARG_WITH(
    [item-id-cap],
    [AS_HELP_STRING(
        [--with-item-id-cap=VALUE],
        [active item ID cap, e.g. 0xCE for starter content])],
    [AS_CASE(
        [$withval],
        [yes|no], [AC_MSG_ERROR([--with-item-id-cap requires a value])],
        [fe8_item_id_cap=$withval])],
    [fe8_item_id_cap=])

AC_ARG_WITH(
    [text-shift],
    [AS_HELP_STRING(
        [--with-text-shift=BYTES],
        [4-byte-aligned modern link-time text shift])],
    [AS_CASE(
        [$withval],
        [yes|no], [AC_MSG_ERROR([--with-text-shift requires a value])],
        [fe8_text_shift=$withval])],
    [fe8_text_shift=])

AS_IF(
    [test -n "$fe8_mechanics_hooks"],
    [FE8_CFG_MECHANICS_HOOKS="EXPANSION_MECHANICS_HOOKS := $fe8_mechanics_hooks"],
    [FE8_CFG_MECHANICS_HOOKS=])
AS_IF(
    [test -n "$fe8_mechanics_sample"],
    [FE8_CFG_MECHANICS_SAMPLE="EXPANSION_MECHANICS_SAMPLE := $fe8_mechanics_sample"],
    [FE8_CFG_MECHANICS_SAMPLE=])
AS_IF(
    [test -n "$fe8_danger_overlay_menu"],
    [FE8_CFG_DANGER_OVERLAY_MENU="EXPANSION_DANGER_OVERLAY_MENU := $fe8_danger_overlay_menu"],
    [FE8_CFG_DANGER_OVERLAY_MENU=])
AS_IF(
    [test -n "$fe8_blue_phase_delegate"],
    [FE8_CFG_BLUE_PHASE_DELEGATE="EXPANSION_BLUE_PHASE_DELEGATE := $fe8_blue_phase_delegate"],
    [FE8_CFG_BLUE_PHASE_DELEGATE=])
AS_IF(
    [test -n "$fe8_starter_content"],
    [FE8_CFG_STARTER_CONTENT="EXPANSION_STARTER_CONTENT := $fe8_starter_content"],
    [FE8_CFG_STARTER_CONTENT=])
AS_IF(
    [test -n "$fe8_aoe_reference"],
    [FE8_CFG_AOE_REFERENCE="EXPANSION_AOE_REFERENCE := $fe8_aoe_reference"],
    [FE8_CFG_AOE_REFERENCE=])
AS_IF(
    [test -n "$fe8_custom_spell_effects"],
    [FE8_CFG_CUSTOM_SPELL_EFFECTS="EXPANSION_CUSTOM_SPELL_EFFECTS := $fe8_custom_spell_effects"],
    [FE8_CFG_CUSTOM_SPELL_EFFECTS=])
AS_IF(
    [test -n "$fe8_asset_manifest"],
    [FE8_CFG_ASSET_MANIFEST="ASSET_MANIFEST := $fe8_asset_manifest"],
    [FE8_CFG_ASSET_MANIFEST=])
AS_IF(
    [test -n "$fe8_localized_text_auto_wrap"],
    [FE8_CFG_LOCALIZED_TEXT_AUTO_WRAP="EXPANSION_LOCALIZED_TEXT_AUTO_WRAP := $fe8_localized_text_auto_wrap"],
    [FE8_CFG_LOCALIZED_TEXT_AUTO_WRAP=])
AS_IF(
    [test -n "$fe8_casual_mode"],
    [FE8_CFG_CASUAL_MODE="EXPANSION_CASUAL_MODE := $fe8_casual_mode"],
    [FE8_CFG_CASUAL_MODE=])
AS_IF(
    [test -n "$fe8_hq_mixer"],
    [FE8_CFG_HQ_MIXER="EXPANSION_HQ_MIXER := $fe8_hq_mixer"],
    [FE8_CFG_HQ_MIXER=])
AS_IF(
    [test -n "$fe8_autoplay_strategies"],
    [FE8_CFG_AUTOPLAY_STRATEGIES="EXPANSION_AUTOPLAY_STRATEGIES := $fe8_autoplay_strategies"],
    [FE8_CFG_AUTOPLAY_STRATEGIES=])
AS_IF(
    [test -n "$fe8_bgm_continuation_policy"],
    [FE8_CFG_BGM_CONTINUATION_POLICY="EXPANSION_BGM_CONTINUATION_POLICY := $fe8_bgm_continuation_policy"],
    [FE8_CFG_BGM_CONTINUATION_POLICY=])
AS_IF(
    [test -n "$fe8_pseudo_locale"],
    [FE8_CFG_PSEUDO_LOCALE="EXPANSION_PSEUDO_LOCALE := $fe8_pseudo_locale"],
    [FE8_CFG_PSEUDO_LOCALE=])
AS_IF(
    [test -n "$fe8_enabled_locales"],
    [FE8_CFG_ENABLED_LOCALES="EXPANSION_ENABLED_LOCALES := $fe8_enabled_locales"],
    [FE8_CFG_ENABLED_LOCALES=])
AS_IF(
    [test -n "$fe8_default_locale"],
    [FE8_CFG_DEFAULT_LOCALE="EXPANSION_DEFAULT_LOCALE := $fe8_default_locale"],
    [FE8_CFG_DEFAULT_LOCALE=])
AS_IF(
    [test -n "$fe8_rom_size"],
    [FE8_CFG_ROM_SIZE="MODERN_ROM_SIZE := $fe8_rom_size"],
    [FE8_CFG_ROM_SIZE=])
AS_IF(
    [test -n "$fe8_item_id_cap"],
    [FE8_CFG_ITEM_ID_CAP="FE8_ITEM_ID_CAP := $fe8_item_id_cap"],
    [FE8_CFG_ITEM_ID_CAP=])
AS_IF(
    [test -n "$fe8_text_shift"],
    [FE8_CFG_TEXT_SHIFT="MODERN_TEXT_SHIFT := $fe8_text_shift"],
    [FE8_CFG_TEXT_SHIFT=])

AC_SUBST([FE8_CFG_MECHANICS_HOOKS])
AC_SUBST([FE8_CFG_MECHANICS_SAMPLE])
AC_SUBST([FE8_CFG_DANGER_OVERLAY_MENU])
AC_SUBST([FE8_CFG_BLUE_PHASE_DELEGATE])
AC_SUBST([FE8_CFG_STARTER_CONTENT])
AC_SUBST([FE8_CFG_AOE_REFERENCE])
AC_SUBST([FE8_CFG_CUSTOM_SPELL_EFFECTS])
AC_SUBST([FE8_CFG_ASSET_MANIFEST])
AC_SUBST([FE8_CFG_LOCALIZED_TEXT_AUTO_WRAP])
AC_SUBST([FE8_CFG_CASUAL_MODE])
AC_SUBST([FE8_CFG_HQ_MIXER])
AC_SUBST([FE8_CFG_AUTOPLAY_STRATEGIES])
AC_SUBST([FE8_CFG_BGM_CONTINUATION_POLICY])
AC_SUBST([FE8_CFG_PSEUDO_LOCALE])
AC_SUBST([FE8_CFG_ENABLED_LOCALES])
AC_SUBST([FE8_CFG_DEFAULT_LOCALE])
AC_SUBST([FE8_CFG_ROM_SIZE])
AC_SUBST([FE8_CFG_ITEM_ID_CAP])
AC_SUBST([FE8_CFG_TEXT_SHIFT])

fe8_validate_rom_size=${fe8_rom_size:-16M}
fe8_validate_text_shift=${fe8_text_shift:-0}
fe8_abs_srcdir=`cd "$srcdir" && pwd`

AC_MSG_CHECKING([configured expansion profile])
fe8_config_output=`PYTHONPATH="$fe8_abs_srcdir${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON" "$fe8_abs_srcdir/scripts/modernize/expansion_config.py" validate \
    --config-mk "$fe8_abs_srcdir/config.mk" \
    --config release \
    --abi aapcs \
    --rom-size "$fe8_validate_rom_size" \
    --text-shift "$fe8_validate_text_shift" \
    --repo-root "$fe8_abs_srcdir" \
    --enabled-locales "$fe8_enabled_locales" \
    --default-locale "$fe8_default_locale" \
    --pseudo-locale "$fe8_pseudo_locale" \
    --mechanics-hooks "$fe8_mechanics_hooks" \
    --mechanics-sample "$fe8_mechanics_sample" \
    --danger-overlay-menu "$fe8_danger_overlay_menu" \
    --blue-phase-delegate "$fe8_blue_phase_delegate" \
    --starter-content "$fe8_starter_content" \
    --aoe-reference "$fe8_aoe_reference" \
    --custom-spell-effects "$fe8_custom_spell_effects" \
    --asset-manifest "$fe8_asset_manifest" \
    --localized-text-auto-wrap "$fe8_localized_text_auto_wrap" \
    --casual-mode "$fe8_casual_mode" \
    --hq-mixer "$fe8_hq_mixer" \
    --autoplay-strategies "$fe8_autoplay_strategies" \
    --bgm-continuation-policy "$fe8_bgm_continuation_policy" \
    --item-id-cap "$fe8_item_id_cap" 2>&1`
fe8_config_status=$?
AS_IF(
    [test "$fe8_config_status" -eq 0],
    [AC_MSG_RESULT([valid])],
    [AC_MSG_RESULT([invalid])
     fe8_config_output=${fe8_config_output#error: }
     AC_MSG_ERROR([$fe8_config_output])])

AC_CONFIG_FILES([
    GNUmakefile:GNUmakefile.in
    config.autotools.mk:config.autotools.mk.in
])
AC_MSG_NOTICE([configuration saved in config.autotools.mk])
AC_MSG_NOTICE([run 'make' to build the configured modern release ROM])
AC_OUTPUT
