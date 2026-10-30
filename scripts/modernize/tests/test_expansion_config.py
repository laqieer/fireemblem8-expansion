"""Tests for scripts/modernize/expansion_config.py (issue #8).

Covers: field validators (valid + malformed title/game code/maker code/
revision/ROM size/semantic version/build-id/preset/ABI/text shift),
config.mk parsing, the override-parameter precedence in load_identity()
(an explicit override always wins over config.mk's parsed value -- this is
the fix for the "two sources of truth" bug), build-commit resolution
precedence (explicit override > git rev-parse HEAD > "unknown" sentinel),
fingerprint determinism, generate_metadata_files()'s write-if-changed
behavior, and the validate/resolve/generate CLI subcommands (including
early, actionable rejection of invalid inputs and incompatible
combinations before any file is written).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "modernize"))

import expansion_config as ec  # noqa: E402
from scripts.modernize.tests.make_database import make_database_variable


def assert_rejects_wrong_length(test_case, validator, values):
    for value in values:
        with test_case.subTest(value=value):
            with test_case.assertRaises(ec.ConfigError):
                validator(value)


def write_config_mk(
    directory: Path,
    version_major="0",
    version_minor="1",
    version_patch="0",
    rom_title="FIREEMBLEM2E",
    rom_game_code="BE8E",
    rom_maker_code="01",
    rom_revision="0",
    build_id="",
    save_compat_epoch="1",
    enabled_locales="en",
    default_locale="en",
    pseudo_locale="0",
    mechanics_hooks=None,
    mechanics_sample=None,
    danger_overlay_menu=None,
    autoplay_strategies=None,
    blue_phase_delegate=None,
) -> Path:
    path = directory / "config.mk"
    # The issue #6 starter-feature flags are optional config.mk keys: a
    # fixture omits them (they default to 0, exercising the absent path)
    # unless a test passes an explicit value.
    feature_lines = []
    if mechanics_hooks is not None:
        feature_lines.append(f"EXPANSION_MECHANICS_HOOKS := {mechanics_hooks}")
    if mechanics_sample is not None:
        feature_lines.append(f"EXPANSION_MECHANICS_SAMPLE := {mechanics_sample}")
    if danger_overlay_menu is not None:
        feature_lines.append(
            f"EXPANSION_DANGER_OVERLAY_MENU := {danger_overlay_menu}"
        )
    if autoplay_strategies is not None:
        feature_lines.append(
            f"EXPANSION_AUTOPLAY_STRATEGIES := {autoplay_strategies}"
        )
    if blue_phase_delegate is not None:
        feature_lines.append(
            f"EXPANSION_BLUE_PHASE_DELEGATE := {blue_phase_delegate}"
        )
    path.write_text(
        "\n".join(
            [
                f"EXPANSION_VERSION_MAJOR := {version_major}",
                f"EXPANSION_VERSION_MINOR := {version_minor}",
                f"EXPANSION_VERSION_PATCH := {version_patch}",
                f"EXPANSION_ROM_TITLE := {rom_title}",
                f"EXPANSION_ROM_GAME_CODE := {rom_game_code}",
                f"EXPANSION_ROM_MAKER_CODE := {rom_maker_code}",
                f"EXPANSION_ROM_REVISION := {rom_revision}",
                f"EXPANSION_BUILD_ID := {build_id}",
                f"EXPANSION_SAVE_COMPAT_EPOCH := {save_compat_epoch}",
                f"EXPANSION_ENABLED_LOCALES := {enabled_locales}",
                f"EXPANSION_DEFAULT_LOCALE := {default_locale}",
                f"EXPANSION_PSEUDO_LOCALE := {pseudo_locale}",
                *feature_lines,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


class ProjectSelectionIdentityTests(unittest.TestCase):
    def setUp(self):
        projects = ROOT / "examples"
        projects.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="selection-", dir=projects)
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        (self.project / "project.mk").write_text(
            "FE8_PROJECT_PROFILES := primary alternate\n"
            "FE8_PROJECT_DEFAULT_PROFILE := primary\n"
            "FE8_PROJECT_REQUIRED_SETTINGS := MODERN_ROM_SIZE EXPANSION_ROM_TITLE\n"
            "FE8_PROJECT_primary_MODERN_ROM_SIZE := 32M\n"
            "FE8_PROJECT_primary_EXPANSION_ROM_TITLE := SELECTEDGAME\n"
            "FE8_PROJECT_alternate_MODERN_ROM_SIZE := 16M\n"
            "FE8_PROJECT_alternate_EXPANSION_ROM_TITLE := ALTERNATE\n"
            "FE8_PROJECT_INHERIT := $(FE8_PROJECT_MODULES)\n",
            encoding="utf-8",
        )

    def test_project_default_profile_supplies_its_effective_settings(self):
        identity = ec.load_identity(
            ROOT / "config.mk",
            config_preset="release",
            abi="aapcs",
            rom_size=None,
            repo_root=ROOT,
            project=self.project.name,
        )
        self.assertEqual(identity.rom_title, "SELECTEDGAME")
        self.assertEqual(identity.rom_size_bytes, 32 * 1024 * 1024)
        self.assertEqual(identity.project_profile, "primary")

    def test_explicit_overrides_cannot_contradict_the_selected_profile(self):
        for overrides in ({"rom_title": "OTHER"}, {"rom_size": "16M"}):
            with self.subTest(overrides=overrides):
                settings = {
                    "config_preset": "release",
                    "abi": "aapcs",
                    "rom_size": None,
                    "repo_root": ROOT,
                    "project": self.project.name,
                    **overrides,
                }
                with self.assertRaises(ec.ConfigError):
                    ec.load_identity(ROOT / "config.mk", **settings)

    def test_project_profile_supplies_locale_and_save_contract_settings(self):
        declaration = self.project / "project.mk"
        with declaration.open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REQUIRED_SETTINGS += EXPANSION_ENABLED_LOCALES "
                "EXPANSION_DEFAULT_LOCALE EXPANSION_SAVE_COMPAT_EPOCH\n"
                "FE8_PROJECT_primary_EXPANSION_ENABLED_LOCALES := ja,en\n"
                "FE8_PROJECT_primary_EXPANSION_DEFAULT_LOCALE := ja\n"
                "FE8_PROJECT_primary_EXPANSION_SAVE_COMPAT_EPOCH := 256\n"
            )
        identity = ec.load_identity(
            ROOT / "config.mk",
            config_preset="release",
            abi="aapcs",
            rom_size=None,
            repo_root=ROOT,
            project=self.project.name,
        )
        self.assertEqual(identity.enabled_locales, ("en", "ja"))
        self.assertEqual(identity.default_locale, "ja")
        self.assertEqual(identity.save_compat_epoch, 256)
        equivalent = ec.load_identity(
            ROOT / "config.mk", config_preset="release", abi="aapcs",
            rom_size="32M", repo_root=ROOT, project=self.project.name,
            enabled_locales="en,ja", default_locale="ja", save_compat_epoch="0x100",
        )
        self.assertEqual(identity.config_fingerprint, equivalent.config_fingerprint)

    def test_selected_project_is_recorded_by_the_real_metadata_cli(self):
        output = self.project / "metadata"
        result = subprocess.run(
            [
                sys.executable, str(ROOT / "scripts/modernize/expansion_config.py"),
                "generate", "--config-mk", str(ROOT / "config.mk"),
                "--config", "release", "--abi", "aapcs", "--rom-size", "32M",
                "--repo-root", str(ROOT),
                "--project", self.project.name,
                "--output-dir", str(output),
            ],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads(
            (output / "expansion_build_metadata.json").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["project"], self.project.name)
        self.assertEqual(metadata["project_profile"], "primary")
        self.assertEqual(metadata["rom_title"], "SELECTEDGAME")

    def test_make_generates_the_selected_profile_metadata_in_its_own_namespace(self):
        project_output = ROOT / "build/projects" / self.project.name
        self.assertFalse(project_output.exists())
        self.addCleanup(
            lambda: shutil.rmtree(project_output) if project_output.exists() else None
        )
        metadata_path = (
            project_output / "primary/release/aapcs/generated/expansion_build_metadata.json"
        )
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "NODEP=1",
                f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release",
                str(metadata_path.relative_to(ROOT)),
            ],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"], self.project.name)
        self.assertEqual(metadata["project_profile"], "primary")
        self.assertEqual(metadata["rom_title"], "SELECTEDGAME")
        self.assertEqual(metadata["rom_size_bytes"], 32 * 1024 * 1024)

    def test_configure_persists_selection_not_derived_preset_values(self):
        from scripts.modernize.tests.test_build_default_lane import parse_make_assignments

        build = self.project / "configured"
        build.mkdir()
        configured = subprocess.run(
            [
                str(ROOT / "configure"),
                f"--with-project={self.project.name}",
                "--with-project-profile=alternate",
            ],
            cwd=build, text=True, capture_output=True, check=False,
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        values = parse_make_assignments(
            (build / "config.autotools.mk").read_text(encoding="utf-8")
        )
        self.assertEqual(
            values,
            {"FE8_PROJECT": self.project.name, "FE8_PROJECT_PROFILE": "alternate"},
        )
        project_output = ROOT / "build/projects" / self.project.name
        self.assertFalse(project_output.exists())
        self.addCleanup(
            lambda: shutil.rmtree(project_output) if project_output.exists() else None
        )
        metadata_path = (
            project_output / "alternate/release/aapcs/generated/expansion_build_metadata.json"
        )
        generated = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "NODEP=1", "MODERN_CONFIG=release",
                str(metadata_path.relative_to(ROOT)),
            ],
            cwd=build, text=True, capture_output=True, check=False,
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["rom_title"], "ALTERNATE")
        self.assertEqual(metadata["rom_size_bytes"], 16 * 1024 * 1024)
        self.assertEqual(metadata["project_profile"], "alternate")
        default_metadata = (
            ROOT / "build/expansion-modern/release/aapcs/generated/expansion_build_metadata.json"
        )
        switched = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "NODEP=1",
                "FE8_PROJECT=default", "MODERN_CONFIG=release",
                str(default_metadata.relative_to(ROOT)),
            ],
            cwd=build, text=True, capture_output=True, check=False,
        )
        self.assertEqual(switched.returncode, 0, switched.stderr)
        default = json.loads(default_metadata.read_text(encoding="utf-8"))
        self.assertNotIn("project", default)
        self.assertEqual(default["rom_title"], "FIREEMBLEM2E")
        self.assertEqual(default["rom_size_bytes"], 16 * 1024 * 1024)

    def test_project_asset_selection_is_used_by_the_public_identity_resolver(self):
        manifest = self.project / "assets.json"
        manifest.write_text(
            json.dumps({"assets": [{"kind": "custom-spell-effect"}]}),
            encoding="utf-8",
        )
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := assets\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_SOURCE_assets := {manifest.relative_to(ROOT)}\n"
            )
        with self.assertRaises(ec.ConfigError):
            ec.load_identity(
                ROOT / "config.mk", config_preset="release", abi="aapcs",
                rom_size=None, repo_root=ROOT, project=self.project.name,
            )

    def test_selected_data_replaces_the_actual_generated_table_without_source_drift(self):
        source = self.project / "characters.json"
        data = json.loads((ROOT / "src/data/characters.json").read_text(encoding="utf-8"))
        data["characters"][0]["base"]["hp"] = 23
        source.write_text(json.dumps(data), encoding="utf-8")
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := characters\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_SOURCE_characters := {source.relative_to(ROOT)}\n"
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        tracked = subprocess.run(
            ["git", "diff", "--", "src", "include", "reports"],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout
        from scripts.generated_data.character_refs import read_character_designators
        from scripts.generated_data.characters.parser import parse_hand_written

        configured = self.project / "configured"
        configured.mkdir()
        configuration = subprocess.run(
            [str(ROOT / "configure"), f"--with-project={self.project.name}",
             "--with-project-profile=alternate"],
            cwd=configured, text=True, capture_output=True, check=False,
        )
        self.assertEqual(configuration.returncode, 0, configuration.stderr)
        default = ROOT / "build/generated/data/data_characters.c"
        for cwd, profile in ((ROOT, "primary"), (configured, "alternate")):
            baseline = None
            for selection, expected_hp in (("default", 0), (self.project.name, 23), ("default", 0)):
                with self.subTest(workflow=profile, selection=selection):
                    generated = (
                        default if selection == "default"
                        else output / f"{profile}/release/aapcs/generated/data/data_characters.c"
                    )
                    result = subprocess.run(
                        [
                            "make", "--no-print-directory", "-s", "NODEP=1",
                            f"FE8_PROJECT={selection}", "MODERN_CONFIG=release",
                            str(generated.relative_to(ROOT)),
                        ],
                        cwd=cwd, text=True, capture_output=True, check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    record = parse_hand_written(
                        str(generated), read_character_designators(), {1})[1]
                    self.assertEqual(record["tuple"][8][0], expected_hp)
                    if selection == "default":
                        if baseline is None:
                            baseline = generated.read_bytes()
                        self.assertEqual(generated.read_bytes(), baseline)
        self.assertEqual(
            subprocess.run(
                ["git", "diff", "--", "src", "include", "reports"],
                cwd=ROOT, capture_output=True, check=True,
            ).stdout,
            tracked,
        )

    @unittest.skipUnless(shutil.which("arm-none-eabi-gcc"), "ARM compiler required")
    def test_selected_code_is_compiled_in_the_original_logical_object_slot(self):
        source = self.project / "time.c"
        source.write_text(
            '#include "global.h"\nu32 GetGameClock(void) { return 73; }\n',
            encoding="utf-8",
        )
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := code\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_CODE_REPLACEMENTS := src/time.c={source.relative_to(ROOT)}\n"
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        for config in ("debug", "release"):
            with self.subTest(config=config):
                obj = output / f"primary/{config}/aapcs/src/time.o"
                result = subprocess.run(
                    [
                        "make", "--no-print-directory", "-s", "NODEP=1",
                        f"FE8_PROJECT={self.project.name}", f"MODERN_CONFIG={config}",
                        str(obj.relative_to(ROOT)),
                    ],
                    cwd=ROOT, text=True, capture_output=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                linked = self.project / f"selected-code-{config}.o"
                subprocess.run(
                    ["arm-none-eabi-ld", "-r", str(obj), "-o", str(linked)],
                    text=True, capture_output=True, check=True,
                )
                symbols = subprocess.run(
                    ["arm-none-eabi-nm", "--defined-only", str(linked)],
                    text=True, capture_output=True, check=True,
                )
                functions = {
                    line.split()[-1]
                    for line in symbols.stdout.splitlines()
                    if len(line.split()) == 3 and line.split()[1] in ("T", "t")
                }
                self.assertEqual(functions, {"GetGameClock"})

    @unittest.skipUnless(shutil.which("arm-none-eabi-gcc"), "ARM compiler required")
    def test_selected_english_source_generates_and_compiles_its_own_message_header(self):
        source = self.project / "texts.txt"
        source.write_text(
            '#include "../../texts/texts.txt"\n'
            "## MSG_PROJECT_SELECTION_PROBE\nSelected project.[X]\n",
            encoding="utf-8",
        )
        code = self.project / "message.c"
        code.write_text(
            '#include "global.h"\n#include "constants/msg.h"\n'
            "const u32 gProjectMessageId = MSG_PROJECT_SELECTION_PROBE;\n",
            encoding="utf-8",
        )
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := game-text code\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_TEXT_MAIN := {source.relative_to(ROOT)}\n"
                "FE8_PROJECT_TEXT_DEFS := texts/textdefs.txt\n"
                f"FE8_PROJECT_CODE_REPLACEMENTS := src/time.c={code.relative_to(ROOT)}\n"
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        profile = output / "primary/release/aapcs"
        generated = profile / "generated/text/msg_data.c"
        obj = profile / "src/time.o"
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "NODEP=1",
                f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release",
                str(generated.relative_to(ROOT)), str(obj.relative_to(ROOT)),
            ],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        from scripts.generated_data.validators import extract_define_constants

        header = profile / "generated/text/include/constants/msg.h"
        constants = extract_define_constants(str(header))
        self.assertEqual(constants["MSG_PROJECT_SELECTION_PROBE"][0], 3414)
        self.assertEqual(constants["MSG_COUNT"][0], 3415)
        binary = self.project / "message.bin"
        subprocess.run(
            ["arm-none-eabi-objcopy", "-O", "binary", "-j", ".rodata", str(obj), str(binary)],
            text=True, capture_output=True, check=True,
        )
        self.assertEqual(binary.read_bytes(), (3414).to_bytes(4, "little"))

    def test_custom_build_roots_do_not_bypass_the_complete_project_namespace(self):
        variables = (
            "MODERN_OUTPUT_DIR", "GENERATED_DATA_OUT_DIR", "LOCALIZATION_OUT_DIR",
            "GAME_LOCALIZATION_OUT_DIR", "MODERN_LOCALIZATION_GENERATED_DIR",
            "MODERN_GAME_LOCALIZATION_GENERATED_DIR", "ASSET_OUTPUT_DIR",
            "ASSET_DISCOVERY_ROOT", "BANIM_OBJECT", "MODERN_ELF_BANIM_SYM",
        )
        output = ROOT / "build/selection-root/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "-f", "Makefile", "-f", "-", "NODEP=1",
                f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release",
                "MODERN_BUILD_ROOT=build/selection-root", "project-output-probe",
            ],
            input="project-output-probe:\n\t@printf '%s\\n' "
            + " ".join(f"'{variable}=$({variable})'" for variable in variables) + "\n",
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        paths = dict(
            line.split("=", 1) for line in result.stdout.splitlines()
            if line.split("=", 1)[0] in variables
        )
        prefix = (
            f"build/selection-root/projects/{self.project.name}/primary/release/aapcs/"
        )
        for variable in variables:
            with self.subTest(variable=variable):
                self.assertTrue(Path(paths[variable]).is_relative_to(Path(prefix)), paths[variable])
        for variable, value in (
            ("FE8_PROJECT_OUTPUT_DIR", "build/outside"),
            ("MODERN_OUTPUT_DIR", "build/outside"),
            ("GENERATED_DATA_OUT_DIR", "build/outside"),
            ("ASSET_OUTPUT_DIR", "build/outside"),
            ("MODERN_BUILD_ROOT", ""),
        ):
            with self.subTest(override=variable):
                rejected = subprocess.run(
                    [
                        "make", "--no-print-directory", "-s", "-f", "project_selection.mk",
                        f"FE8_PROJECT={self.project.name}", f"{variable}={value}",
                        "project-selection-config",
                    ],
                    cwd=ROOT, text=True, capture_output=True, check=False,
                )
                self.assertNotEqual(rejected.returncode, 0)

    def test_selected_ui_catalog_reaches_the_linked_catalog_descriptor(self):
        catalog = self.project / "catalog.en.json"
        data = json.loads(
            (ROOT / "texts/expansion/catalog.en.json").read_text(encoding="utf-8")
        )
        data["strings"]["framework.title"] = "Selected Project"
        data["strings"]["project.title"] = "Selected Project"
        catalog.write_text(json.dumps(data), encoding="utf-8")
        registry = self.project / "registry.json"
        registry_data = json.loads(
            (ROOT / "texts/expansion/registry.json").read_text(encoding="utf-8")
        )
        entry = dict(registry_data["messages"][0])
        entry.update(
            id=max(message["id"] for message in registry_data["messages"]) + 1,
            key="project.title",
        )
        registry_data["messages"].append(entry)
        registry.write_text(json.dumps(registry_data), encoding="utf-8")
        presentation = self.project / "presentation.json"
        presentation_data = json.loads(
            (ROOT / "src/data/ui_presentation.json").read_text(encoding="utf-8")
        )
        presentation_data["contexts"][0]["title_key"] = "project.title"
        presentation.write_text(json.dumps(presentation_data), encoding="utf-8")
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := ui-text ui_presentation\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_UI_REGISTRY := {registry.relative_to(ROOT)}\n"
                f"FE8_PROJECT_UI_CATALOGS := en={catalog.relative_to(ROOT)}\n"
                f"FE8_PROJECT_SOURCE_ui_presentation := {presentation.relative_to(ROOT)}\n"
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        generated = output / "primary/release/aapcs/expansion-localization/generated"
        manifest_c = (
            output / "primary/release/aapcs/generated/data/expansion_ui_presentation_manifest.c"
        )
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "NODEP=1",
                f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release",
                str((generated / "expansion_locale_catalog.c").relative_to(ROOT)),
                str(manifest_c.relative_to(ROOT)),
            ],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        driver = self.project / "catalog_driver.c"
        driver.write_text(
            '#include "global.h"\n#include "expansion_locale.h"\n'
            '#include "expansion_msg_ids.h"\n#include "expansion_ui_presentation.h"\n'
            "#include <stdio.h>\n"
            "int main(void) {\n"
            "    unsigned i;\n"
            "    const struct ExpansionLocaleCatalogDescriptor *catalog = &gExpansionLocaleCatalogs[0];\n"
            "    if (gExpansionUiPresentationManifest[0].titleMsgId != EXP_MSG_PROJECT_TITLE)\n"
            "        return 2;\n"
            "    for (i = 0; i < catalog->count; ++i)\n"
            "        if (catalog->ids[i] == EXP_MSG_PROJECT_TITLE) {\n"
            "            puts(catalog->strings[i]);\n"
            "            return 0;\n"
            "        }\n"
            "    return 1;\n"
            "}\n",
            encoding="utf-8",
        )
        binary = self.project / "catalog_driver"
        compiled = subprocess.run(
            [
                "cc", "-std=gnu89", "-DMODERN=1", "-I", str(ROOT / "include"),
                "-I", str(generated), str(driver),
                str(generated / "expansion_locale_catalog.c"), str(manifest_c),
                "-o", str(binary),
            ],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        observed = subprocess.run(
            [str(binary)], text=True, capture_output=True, check=True,
        )
        self.assertEqual(observed.stdout, "Selected Project\n")

    def test_selected_asset_manifest_drives_the_actual_tmx_derivative(self):
        tmx = self.project / "map.tmx"
        tmx.write_text(
            (ROOT / "assets/tmx/Ch2Map.tmx").read_text(encoding="utf-8").replace(
                "521,521,521,", "2,521,521,", 1
            ),
            encoding="utf-8",
        )
        manifest = self.project / "assets.json"
        data = json.loads((ROOT / "assets/manifest.json").read_text(encoding="utf-8"))
        data["assets"] = [data["assets"][0]]
        data["assets"][0]["sources"] = [str(tmx.relative_to(ROOT))]
        manifest.write_text(json.dumps(data), encoding="utf-8")
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := assets\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_SOURCE_assets := {manifest.relative_to(ROOT)}\n"
            )
        environment = {**os.environ, "GIT_INDEX_FILE": str(self.project / "asset.index")}
        subprocess.run(["git", "read-tree", "HEAD"], cwd=ROOT, env=environment, check=True)
        subprocess.run(
            ["git", "add", "--", str(tmx.relative_to(ROOT))],
            cwd=ROOT, env=environment, check=True,
        )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "NODEP=1",
                f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release",
                "assets-generate", "print-ASSET_OUTPUT_DIR",
            ],
            cwd=ROOT, env=environment, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        asset_output = ROOT / result.stdout.splitlines()[-1]
        payload = (asset_output / "tmx/CH2_MAIN_MAP.mar").read_bytes()
        self.assertEqual(len(payload), 450)
        self.assertEqual(int.from_bytes(payload[:2], "little"), 8)

    def test_incomplete_duplicate_unknown_and_inherited_replacement_inputs_fail(self):
        declaration = self.project / "project.mk"
        original = declaration.read_text(encoding="utf-8")
        for invalid in (
            "FE8_PROJECT_INHERIT := code\n",
            "FE8_PROJECT_REPLACE := characters\n",
            "FE8_PROJECT_INHERIT += code\n",
            "FE8_PROJECT_INHERIT += unknown\n",
            "FE8_PROJECT_SOURCE_characters := src/data/characters.json\n",
            "FE8_PROJECT_SOURCE_unknown := src/data/characters.json\n",
            "FE8_PROJECT_REPLACE := characters\n"
            "FE8_PROJECT_INHERIT := $(filter-out characters,$(FE8_PROJECT_MODULES))\n"
            "FE8_PROJECT_SOURCE_characters := examples/no-such-project/characters.json\n",
        ):
            with self.subTest(declaration=invalid):
                declaration.write_text(original + invalid, encoding="utf-8")
                result = subprocess.run(
                    [
                        "make", "--no-print-directory", "-s", "-f", "project_selection.mk",
                        f"FE8_PROJECT={self.project.name}", "project-selection-config",
                    ],
                    cwd=ROOT, text=True, capture_output=True, check=False,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse((ROOT / "build/projects" / self.project.name).exists())
        declaration.write_text(original, encoding="utf-8")
        for project, profile in (
            ("../outside", None), ("bad project", None),
            ("no-such-project", None), (self.project.name, "missing"),
        ):
            with self.subTest(project=project, profile=profile):
                with self.assertRaises(ec.ConfigError):
                    ec.load_identity(
                        ROOT / "config.mk", config_preset="release", abi="aapcs",
                        rom_size=None, repo_root=ROOT, project=project, project_profile=profile,
                    )

    def test_selected_projects_reject_archival_mixed_and_non_aapcs_requests(self):
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        legacy_generated = (
            f"build/projects/{self.project.name}/primary/debug/aapcs/generated/data/data_items.o"
        )
        for goals in (
            ["legacy"], ["all", "fireemblem8.gba"], ["src/time.o"],
            [legacy_generated], ["asm/fe6sio.o"],
        ):
            with self.subTest(goals=goals):
                result = subprocess.run(
                    [
                        "make", "--no-print-directory", "-sn", "NODEP=1",
                        f"FE8_PROJECT={self.project.name}", *goals,
                    ],
                    cwd=ROOT, text=True, capture_output=True, check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("FE8_PROJECT", result.stderr)
                self.assertIn("archival", result.stderr.lower())
                self.assertNotIn("tools/agbcc", result.stdout)
        with self.assertRaises(ec.ConfigError):
            ec.load_identity(
                ROOT / "config.mk", config_preset="release", abi="apcs-gnu",
                rom_size=None, repo_root=ROOT, project=self.project.name,
            )

    @unittest.skipUnless(shutil.which("arm-none-eabi-gcc"), "ARM compiler required")
    def test_selected_font_binary_is_preprocessed_and_compiled_in_its_existing_slot(self):
        widths = self.project / "widths.u8"
        widths.write_bytes(b"\x09")
        source = self.project / "font.c"
        source.write_text(
            '#include "global.h"\n'
            "const u8 gLocalizedFontJaSystemWidths[] = "
            f'INCBIN_U8("{widths.relative_to(ROOT)}");\n',
            encoding="utf-8",
        )
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := fonts\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                "FE8_PROJECT_FONT_REPLACEMENTS := "
                f"src/data/localized_font_data.c={source.relative_to(ROOT)}\n"
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        obj = output / "primary/release/aapcs/src/data/localized_font_data.o"
        depfile = obj.with_suffix(".assets.d")
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "NODEP=1",
                f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release",
                str(depfile.relative_to(ROOT)), str(obj.relative_to(ROOT)),
            ],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(widths.relative_to(ROOT)), depfile.read_text(encoding="utf-8").split())
        binary = self.project / "font.bin"
        subprocess.run(
            ["arm-none-eabi-objcopy", "-O", "binary", "-j", ".rodata", str(obj), str(binary)],
            text=True, capture_output=True, check=True,
        )
        self.assertEqual(binary.read_bytes(), b"\x09")

    def test_selected_item_symbols_resolve_and_validate_against_the_generated_message_header(self):
        text = self.project / "texts.txt"
        text.write_text(
            '#include "../../texts/texts.txt"\n'
            "## MSG_PROJECT_SELECTION_PROBE\nSelected project.[X]\n",
            encoding="utf-8",
        )
        items = self.project / "items.json"
        data = json.loads((ROOT / "src/data/items.json").read_text(encoding="utf-8"))
        data["items"][0]["nameTextId"] = "MSG_PROJECT_SELECTION_PROBE"
        items.write_text(json.dumps(data), encoding="utf-8")
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := game-text items\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_TEXT_MAIN := {text.relative_to(ROOT)}\n"
                "FE8_PROJECT_TEXT_DEFS := texts/textdefs.txt\n"
                f"FE8_PROJECT_SOURCE_items := {items.relative_to(ROOT)}\n"
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        generated = output / "primary/release/aapcs/generated/data/data_items.c"
        command = [
            "make", "--no-print-directory", "-s", "NODEP=1",
            f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release",
            str(generated.relative_to(ROOT)),
        ]
        result = subprocess.run(
            command, cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        from scripts.generated_data.items.parser import parse_hand_written

        record = parse_hand_written(str(generated), ["ITEM_NONE"])["ITEM_NONE"]
        self.assertEqual(record["tuple"][0], 3414)
        previous = generated.read_bytes()
        data["items"][0]["nameTextId"] = "MSG_COUNT"
        items.write_text(json.dumps(data), encoding="utf-8")
        rejected = subprocess.run(
            command, cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(generated.read_bytes(), previous)

    def test_standalone_data_goals_build_and_refresh_the_selected_header(self):
        text = self.project / "texts.txt"
        text.write_text(
            '#include "../../texts/texts.txt"\n'
            "## MSG_PROJECT_DATA_FIRST\nFirst selected text.[X]\n",
            encoding="utf-8",
        )
        items = self.project / "items.json"
        data = json.loads((ROOT / "src/data/items.json").read_text(encoding="utf-8"))
        data["items"][0]["nameTextId"] = "MSG_PROJECT_DATA_FIRST"
        items.write_text(json.dumps(data), encoding="utf-8")
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REPLACE := game-text items\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_TEXT_MAIN := {text.relative_to(ROOT)}\n"
                "FE8_PROJECT_TEXT_DEFS := texts/textdefs.txt\n"
                f"FE8_PROJECT_SOURCE_items := {items.relative_to(ROOT)}\n"
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        header = output / "primary/release/aapcs/generated/text/include/constants/msg.h"
        from scripts.generated_data.validators import extract_define_constants

        for goal, symbol, expected in (
            ("generated-data-validate", "MSG_PROJECT_DATA_FIRST", 3414),
            ("generated-data-manifest", "MSG_PROJECT_DATA_SECOND", 3415),
        ):
            with self.subTest(goal=goal):
                if expected == 3415:
                    with text.open("a", encoding="utf-8") as stream:
                        stream.write("## MSG_PROJECT_DATA_SECOND\nSecond selected text.[X]\n")
                    data["items"][0]["nameTextId"] = symbol
                    items.write_text(json.dumps(data), encoding="utf-8")
                result = subprocess.run(
                    [
                        "make", "--no-print-directory", "-s", "NODEP=1",
                        f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release", goal,
                    ],
                    cwd=ROOT, text=True, capture_output=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(extract_define_constants(str(header))[symbol][0], expected)

    @unittest.skipUnless(
        shutil.which("arm-none-eabi-as") and (ROOT / "tools/scaninc/scaninc").is_file(),
        "ARM assembler and built scaninc required",
    )
    def test_modern_project_link_preparation_builds_real_shared_assembly(self):
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        query = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "-f", "Makefile", "-f", "-", "NODEP=1",
                f"FE8_PROJECT={self.project.name}", "project-shared-input",
            ],
            input="project-shared-input:\n"
            "\t@printf '%s\\n' '$(firstword $(filter sound/songs/%.o,$(MODERN_ELF_LEGACY_ASM)))'\n",
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(query.returncode, 0, query.stderr)
        shared = query.stdout.splitlines()[-1]
        self.assertTrue(shared.startswith("sound/") and shared.endswith(".o"), shared)
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "NODEP=1",
                f"FE8_PROJECT={self.project.name}", "expansion-modern-legacy-ready",
                f"MODERN_ELF_LEGACY_ASM={shared}", "MODERN_ELF_LEGACY_MIDI=",
            ],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        elf = subprocess.run(
            ["arm-none-eabi-readelf", "-h", str(ROOT / shared)],
            text=True, capture_output=True, check=True,
        )
        self.assertRegex(elf.stdout, r"Machine:\s+ARM\b")

    def test_selected_translation_and_mapping_use_the_real_engine_root(self):
        authored = self.project / "authored.fr.json"
        data = json.loads(
            (ROOT / "texts/locales/eu/authored.fr.json").read_text(encoding="utf-8")
        )
        data["strings"]["0x040C"] = "Project proof.[CTRL:001F]"
        authored.write_text(json.dumps(data), encoding="utf-8")
        mapping = self.project / "mapping.json"
        shutil.copyfile(ROOT / "texts/locales/mapping/fe8u_target_map.json", mapping)
        with (self.project / "project.mk").open("a", encoding="utf-8") as stream:
            stream.write(
                "FE8_PROJECT_REQUIRED_SETTINGS += EXPANSION_ENABLED_LOCALES\n"
                "FE8_PROJECT_primary_EXPANSION_ENABLED_LOCALES := en,fr\n"
                "FE8_PROJECT_REPLACE := game-authored-fr game-mapping\n"
                "FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_SOURCE_game-authored-fr := {authored.relative_to(ROOT)}\n"
                f"FE8_PROJECT_SOURCE_game-mapping := {mapping.relative_to(ROOT)}\n"
            )
        output = ROOT / "build/projects" / self.project.name
        self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
        generated = output / "primary/release/aapcs/game-localization/generated"
        catalog = generated / "game_localization_catalog.c"
        result = subprocess.run(
            [
                "make", "--no-print-directory", "-s", "NODEP=1",
                f"FE8_PROJECT={self.project.name}", "MODERN_CONFIG=release",
                str(catalog.relative_to(ROOT)),
                "game-localization-generate", "game-localization-leakage-check",
            ],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        standalone_budget = json.loads(
            (output / "primary/release/aapcs/generated/game-localization/game_localization_budget.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(standalone_budget["enabled_locales"], ["fr"])
        driver = self.project / "game_catalog_driver.c"
        driver.write_text(
            '#include "global.h"\n#include "game_localization_catalog.h"\n'
            '#include "localized_text_codec.h"\n#include <stdio.h>\n'
            "int main(void) {\n"
            "    const struct GameLocalizationLocaleCatalog *catalog = "
            "gGameLocalizationCatalogs[GAME_LOCALIZATION_LOCALE_FR];\n"
            "    const struct GameLocalizationCatalogEntry *entry = &catalog->entries[0x040C];\n"
            "    unsigned char buffer[128];\n"
            "    u32 length;\n"
            "    if (LocalizedTextCodec_Decode(catalog->nodes, catalog->nodeCount,\n"
            "            catalog->rootIndex, entry->data, entry->compressedSize,\n"
            "            entry->bitLength, buffer, sizeof(buffer), &length) != 0)\n"
            "        return 1;\n"
            "    puts((const char *) buffer);\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        binary = self.project / "game_catalog_driver"
        compiled = subprocess.run(
            [
                "cc", "-std=gnu89", "-DMODERN=1",
                "-DFE8_EXPANSION_ENABLED_LOCALE_MASK=0x09u",
                "-I", str(ROOT / "include"), "-I", str(generated),
                str(driver), str(catalog), str(ROOT / "src/localized_text_codec.c"),
                "-o", str(binary),
            ],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        observed = subprocess.run([str(binary)], capture_output=True, check=True)
        self.assertEqual(observed.stdout, b"Project proof.\x1f\n")


@unittest.skipUnless(shutil.which("arm-none-eabi-gcc"), "modern ROM toolchain required")
class ProjectSelectionRomTests(unittest.TestCase):
    def test_selected_project_rom_links_and_boots(self):
        projects = ROOT / "examples"
        projects.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="selection-rom-", dir=projects) as directory:
            project = Path(directory)
            (project / "project.mk").write_text(
                "FE8_PROJECT_PROFILES := probe\n"
                "FE8_PROJECT_DEFAULT_PROFILE := probe\n"
                "FE8_PROJECT_REQUIRED_SETTINGS := MODERN_ROM_SIZE\n"
                "FE8_PROJECT_probe_MODERN_ROM_SIZE := 32M\n"
                "FE8_PROJECT_INHERIT := $(FE8_PROJECT_MODULES)\n",
                encoding="utf-8",
            )
            output = ROOT / "build/projects" / project.name
            self.addCleanup(lambda: shutil.rmtree(output) if output.exists() else None)
            result = subprocess.run(
                [
                    "make", "--no-print-directory", "-s", "-j2",
                    f"FE8_PROJECT={project.name}", "MODERN_CONFIG=debug", "MODERN_ABI=aapcs",
                    "expansion-modern-boot-check",
                ],
                cwd=ROOT, text=True, capture_output=True, check=False, timeout=1800,
            )
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-20000:])
            profile = output / "probe/debug/aapcs"
            self.assertEqual((profile / "fireemblem8.gba").stat().st_size, 32 * 1024 * 1024)
            metadata = json.loads(
                (profile / "generated/expansion_build_metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["project"], project.name)
            self.assertEqual(metadata["project_profile"], "probe")
            pinned = []
            for line in (profile / "fireemblem8.map").read_text(encoding="utf-8").splitlines():
                fields = line.split()
                if (
                    len(fields) == 4 and fields[0] == ".data"
                    and fields[1].startswith("0x") and fields[2].startswith("0x")
                    and int(fields[1], 16) == 0x08C02000 and int(fields[2], 16) > 0
                ):
                    pinned.append((ROOT / fields[3]).resolve())
            self.assertEqual(pinned, [(profile / "banim/data_banim.o").resolve()])


class ValidateTitleTests(unittest.TestCase):
    def test_valid_title_passes_through(self):
        self.assertEqual(ec.validate_title("FIREEMBLEM2E"), "FIREEMBLEM2E")

    def test_exactly_12_bytes_is_ok(self):
        self.assertEqual(ec.validate_title("ABCDEFGHIJKL"), "ABCDEFGHIJKL")

    def test_empty_title_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_title("")

    def test_too_long_title_rejected(self):
        with self.assertRaises(ec.ConfigError) as ctx:
            ec.validate_title("THISISTOOLONG")
        self.assertIn("EXPANSION_ROM_TITLE", str(ctx.exception))

    def test_non_ascii_title_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_title("FE8\u00e9")


class ValidateGameCodeTests(unittest.TestCase):
    def test_valid_game_code_passes_through(self):
        self.assertEqual(ec.validate_game_code("BE8E"), "BE8E")

    def test_wrong_length_rejected(self):
        assert_rejects_wrong_length(self, ec.validate_game_code, ("ABC", "ABCDE"))

    def test_non_ascii_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_game_code("B\u00e98E")


class ValidateMakerCodeTests(unittest.TestCase):
    def test_valid_maker_code_passes_through(self):
        self.assertEqual(ec.validate_maker_code("01"), "01")

    def test_wrong_length_rejected(self):
        assert_rejects_wrong_length(self, ec.validate_maker_code, ("0", "012"))


class ValidateRevisionTests(unittest.TestCase):
    def test_boundary_values_ok(self):
        self.assertEqual(ec.validate_revision(0), 0)
        self.assertEqual(ec.validate_revision(255), 255)
        self.assertEqual(ec.validate_revision("42"), 42)

    def test_out_of_range_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_revision(256)
        with self.assertRaises(ec.ConfigError):
            ec.validate_revision(-1)

    def test_non_integer_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_revision("not-a-number")


class ValidateRomSizeTests(unittest.TestCase):
    def test_named_sizes_case_insensitive(self):
        self.assertEqual(ec.validate_rom_size("16M"), 16 * 1024 * 1024)
        self.assertEqual(ec.validate_rom_size("16m"), 16 * 1024 * 1024)
        self.assertEqual(ec.validate_rom_size("32M"), 32 * 1024 * 1024)

    def test_exact_byte_counts_accepted(self):
        self.assertEqual(ec.validate_rom_size(str(32 * 1024 * 1024)), 32 * 1024 * 1024)

    def test_unsupported_size_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_rom_size("8M")
        with self.assertRaises(ec.ConfigError):
            ec.validate_rom_size("12345")

    def test_garbage_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_rom_size("banana")


class ValidateVersionTests(unittest.TestCase):
    def test_valid_version_components(self):
        self.assertEqual(ec.validate_version(1, 2, 3), (1, 2, 3))
        self.assertEqual(ec.validate_version("0", "1", "0"), (0, 1, 0))

    def test_boundary_values_ok(self):
        self.assertEqual(ec.validate_version(0, 0, 0), (0, 0, 0))
        self.assertEqual(ec.validate_version(255, 255, 255), (255, 255, 255))

    def test_out_of_range_component_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_version(256, 0, 0)
        with self.assertRaises(ec.ConfigError):
            ec.validate_version(0, -1, 0)

    def test_non_integer_component_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_version("x", 0, 0)


class ValidatePresetAbiTests(unittest.TestCase):
    def test_supported_presets_ok(self):
        self.assertEqual(ec.validate_preset("debug"), "debug")
        self.assertEqual(ec.validate_preset("release"), "release")

    def test_unsupported_preset_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_preset("profiling")

    def test_supported_abis_ok(self):
        self.assertEqual(ec.validate_abi("aapcs"), "aapcs")
        self.assertEqual(ec.validate_abi("apcs-gnu"), "apcs-gnu")

    def test_unsupported_abi_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_abi("eabi")


class ValidateTextShiftTests(unittest.TestCase):
    def test_aligned_value_ok(self):
        self.assertEqual(ec.validate_text_shift("0"), 0)
        self.assertEqual(ec.validate_text_shift("1024"), 1024)

    def test_unaligned_value_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_text_shift("3")

    def test_non_numeric_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_text_shift("nope")


class ValidateBuildIdOverrideTests(unittest.TestCase):
    def test_none_and_empty_are_ok_and_mean_unset(self):
        self.assertIsNone(ec.validate_build_id_override(None))
        self.assertIsNone(ec.validate_build_id_override(""))

    def test_valid_hex_sha_is_lowercased(self):
        self.assertEqual(ec.validate_build_id_override("ABCDEF12"), "abcdef12")

    def test_short_prefix_ok(self):
        self.assertEqual(ec.validate_build_id_override("abcd"), "abcd")

    def test_timestamp_like_value_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_build_id_override("2024-01-01T00:00:00Z")

    def test_branch_name_like_value_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_build_id_override("feature/my-branch")

    def test_too_short_or_too_long_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_build_id_override("abc")
        with self.assertRaises(ec.ConfigError):
            ec.validate_build_id_override("a" * 41)


class ParseConfigMkTests(unittest.TestCase):
    def test_parses_all_required_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config_mk(Path(tmp))
            values = ec.parse_config_mk(path)
        for key in ec.CONFIG_MK_KEYS:
            self.assertIn(key, values)
        self.assertEqual(values["EXPANSION_ROM_TITLE"], "FIREEMBLEM2E")

    def test_ignores_comments_and_blank_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.mk"
            path.write_text(
                "\n".join(
                    [
                        "# a leading comment",
                        "",
                        "EXPANSION_VERSION_MAJOR := 0  # inline comment",
                        "EXPANSION_VERSION_MINOR := 1",
                        "EXPANSION_VERSION_PATCH := 0",
                        "EXPANSION_ROM_TITLE := FIREEMBLEM2E",
                        "EXPANSION_ROM_GAME_CODE := BE8E",
                        "EXPANSION_ROM_MAKER_CODE := 01",
                        "EXPANSION_ROM_REVISION := 0",
                        "EXPANSION_BUILD_ID :=",
                        "EXPANSION_SAVE_COMPAT_EPOCH := 1",
                        "EXPANSION_ENABLED_LOCALES := en",
                        "EXPANSION_DEFAULT_LOCALE := en",
                        "EXPANSION_PSEUDO_LOCALE := 0",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            values = ec.parse_config_mk(path)
        self.assertEqual(values["EXPANSION_VERSION_MAJOR"], "0")

    def test_missing_file_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.parse_config_mk(Path("/nonexistent/config.mk"))

    def test_missing_required_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.mk"
            path.write_text("EXPANSION_VERSION_MAJOR := 0\n", encoding="utf-8")
            with self.assertRaises(ec.ConfigError) as ctx:
                ec.parse_config_mk(path)
        self.assertIn("EXPANSION_ROM_TITLE", str(ctx.exception))


class ResolveBuildCommitTests(unittest.TestCase):
    def test_explicit_override_wins(self):
        commit = ec.resolve_build_commit("abcd1234", Path("."))
        self.assertEqual(commit, "abcd1234")

    def test_falls_back_to_git_rev_parse_head(self):
        # This repository itself is a git checkout, so HEAD must resolve to
        # a real 40-hex-character commit SHA.
        commit = ec.resolve_build_commit(None, ROOT)
        self.assertRegex(commit, r"^[0-9a-f]{40}$")

    def test_falls_back_to_unknown_sentinel_outside_a_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            commit = ec.resolve_build_commit(None, Path(tmp))
        self.assertEqual(commit, "unknown")

    def test_nested_non_git_tree_never_adopts_outer_repo_head(self):
        """issue #9 remediation regression: a non-git candidate tree
        nested *inside* an unrelated outer Git repository (with its own,
        different HEAD) must resolve to the fixed "unknown" sentinel --
        never git's own upward-directory-discovery-found outer HEAD."""
        with tempfile.TemporaryDirectory() as tmp:
            outer = Path(tmp) / "outer"
            outer.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=str(outer), check=True)
            subprocess.run(
                ["git", "config", "user.email", "outer@example.invalid"], cwd=str(outer), check=True
            )
            subprocess.run(["git", "config", "user.name", "outer"], cwd=str(outer), check=True)
            (outer / "outer-file.txt").write_text("unrelated outer repository content\n")
            subprocess.run(["git", "add", "outer-file.txt"], cwd=str(outer), check=True)
            subprocess.run(["git", "commit", "-q", "-m", "outer commit"], cwd=str(outer), check=True)
            outer_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=str(outer), capture_output=True, text=True, check=True,
            ).stdout.strip()

            candidate = outer / "nested" / "candidate"
            candidate.mkdir(parents=True)
            self.assertFalse((candidate / ".git").exists())

            commit = ec.resolve_build_commit(None, candidate)
            self.assertEqual(commit, "unknown")
            self.assertNotEqual(commit, outer_head)

    def test_nested_non_git_tree_makes_no_git_subprocess_call_at_all(self):
        """The fix must never even *invoke* git for a candidate root with
        no `.git` of its own -- not merely "invoke it, but discard/ignore
        an unwanted result". A patched `subprocess.run` that raises on
        any call proves this empirically."""
        with tempfile.TemporaryDirectory() as tmp:
            outer = Path(tmp) / "outer"
            outer.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=str(outer), check=True)
            candidate = outer / "nested" / "candidate"
            candidate.mkdir(parents=True)

            def _forbidden_run(*args, **kwargs):
                raise AssertionError(f"unexpected subprocess.run call: {args!r} {kwargs!r}")

            with mock.patch.object(ec.subprocess, "run", _forbidden_run):
                commit = ec.resolve_build_commit(None, candidate)
            self.assertEqual(commit, "unknown")


class FingerprintDeterminismTests(unittest.TestCase):
    def test_same_inputs_produce_same_fingerprint(self):
        fields = {"version": [0, 1, 0], "abi": "aapcs"}
        self.assertEqual(ec.compute_fingerprint(fields), ec.compute_fingerprint(dict(fields)))

    def test_key_order_does_not_affect_fingerprint(self):
        a = {"abi": "aapcs", "version": [0, 1, 0]}
        b = {"version": [0, 1, 0], "abi": "aapcs"}
        self.assertEqual(ec.compute_fingerprint(a), ec.compute_fingerprint(b))

    def test_different_inputs_produce_different_fingerprints(self):
        a = {"abi": "aapcs"}
        b = {"abi": "apcs-gnu"}
        self.assertNotEqual(ec.compute_fingerprint(a), ec.compute_fingerprint(b))

    def test_debug_and_release_presets_produce_different_fingerprints(self):
        base = {
            "version": [0, 1, 0], "abi": "aapcs", "rom_size_bytes": 16 * 1024 * 1024,
            "text_shift": 0, "rom_title": "FIREEMBLEM2E", "rom_game_code": "BE8E",
            "rom_maker_code": "01", "rom_revision": 0,
        }
        debug = ec.compute_fingerprint(dict(base, config_preset="debug"))
        release = ec.compute_fingerprint(dict(base, config_preset="release"))
        self.assertNotEqual(debug, release)


class LoadIdentityTests(unittest.TestCase):
    def test_defaults_come_from_config_mk(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            identity = ec.load_identity(
                config_mk_path=config_mk,
                config_preset="debug",
                abi="aapcs",
                rom_size="16M",
                repo_root=Path(tmp),
            )
        self.assertEqual(identity.rom_title, "FIREEMBLEM2E")
        self.assertEqual(identity.rom_game_code, "BE8E")
        self.assertEqual(identity.version_string, "0.1.0")
        self.assertEqual(identity.build_commit, "unknown")

    def test_override_wins_over_config_mk_value(self):
        """The fix for the two-sources-of-truth bug: an explicit override
        parameter must take precedence over config.mk's parsed value."""
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            identity = ec.load_identity(
                config_mk_path=config_mk,
                config_preset="debug",
                abi="aapcs",
                rom_size="16M",
                repo_root=Path(tmp),
                rom_title="FE8CUSTOMTST",
                rom_game_code="ZZZZ",
                rom_maker_code="9X",
                rom_revision=7,
                version_major=2,
            )
        self.assertEqual(identity.rom_title, "FE8CUSTOMTST")
        self.assertEqual(identity.rom_game_code, "ZZZZ")
        self.assertEqual(identity.rom_maker_code, "9X")
        self.assertEqual(identity.rom_revision, 7)
        self.assertEqual(identity.version_major, 2)

    def test_empty_string_override_falls_back_to_config_mk(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            identity = ec.load_identity(
                config_mk_path=config_mk,
                config_preset="debug",
                abi="aapcs",
                rom_size="16M",
                repo_root=Path(tmp),
                rom_title="",
            )
        self.assertEqual(identity.rom_title, "FIREEMBLEM2E")

    def test_invalid_override_is_still_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            with self.assertRaises(ec.ConfigError):
                ec.load_identity(
                    config_mk_path=config_mk,
                    config_preset="debug",
                    abi="aapcs",
                    rom_size="16M",
                    repo_root=Path(tmp),
                    rom_game_code="ABC",
                )

    def test_fingerprint_is_populated_and_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            first = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp),
            )
            second = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp),
            )
        self.assertEqual(first.config_fingerprint, second.config_fingerprint)
        self.assertTrue(first.config_fingerprint)

    def test_debug_vs_release_fingerprints_differ(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            debug = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp),
            )
            release = ec.load_identity(
                config_mk_path=config_mk, config_preset="release", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp),
            )
        self.assertNotEqual(debug.config_fingerprint, release.config_fingerprint)

    def test_item_cap_is_recorded_and_changes_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            default = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp),
            )
            expanded = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp), item_id_cap="0xCE",
            )
        self.assertEqual(default.item_id_cap, 0xCD)
        self.assertEqual(expanded.item_id_cap, 0xCE)
        self.assertEqual(expanded.to_dict()["item_id_cap"], 0xCE)
        self.assertNotEqual(default.config_fingerprint, expanded.config_fingerprint)

    def test_build_id_override_from_config_mk_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp), build_id="deadbeef")
            identity = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp),
            )
        self.assertEqual(identity.build_commit, "deadbeef")

    def test_explicit_build_id_override_wins_over_config_mk(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp), build_id="deadbeef")
            identity = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp), build_id_override="cafef00d",
            )
        self.assertEqual(identity.build_commit, "cafef00d")


class ValidateEnabledLocalesTests(unittest.TestCase):
    def test_single_en_ok(self):
        self.assertEqual(ec.validate_enabled_locales("en"), ("en",))

    def test_en_and_qps_ok_and_normalized_to_stable_order(self):
        # Input order is reversed from the stable id order; the validator
        # must normalize to the fixed stable-id order regardless.
        self.assertEqual(
            ec.validate_enabled_locales("qps-ploc,en"), ("en", "qps-ploc")
        )

    def test_list_input_accepted(self):
        self.assertEqual(ec.validate_enabled_locales(["en", "qps-ploc"]), ("en", "qps-ploc"))

    def test_real_cjk_locales_are_configurable_and_stably_ordered(self):
        expected = {
            "en,ja": ("en", "ja"),
            "en,zh-Hans": ("en", "zh-Hans"),
            "zh-Hans,en,ja": ("en", "ja", "zh-Hans"),
        }
        for configured, normalized in expected.items():
            with self.subTest(configured=configured):
                self.assertEqual(
                    ec.validate_enabled_locales(configured),
                    normalized,
                )

    def test_real_eu_locales_are_configurable_and_stably_ordered(self):
        self.assertEqual(
            ec.validate_enabled_locales("it,es,de,fr,en"),
            ("en", "fr", "de", "es", "it"),
        )

    def test_empty_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_enabled_locales("")

    def test_missing_en_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_enabled_locales("qps-ploc")

    def test_unknown_locale_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_enabled_locales("en,klingon")

    def test_duplicate_locale_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_enabled_locales("en,en")

class ValidateDefaultLocaleTests(unittest.TestCase):
    def test_default_within_enabled_set_ok(self):
        self.assertEqual(ec.validate_default_locale("en", ("en", "qps-ploc")), "en")

    def test_default_not_in_enabled_set_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_default_locale("qps-ploc", ("en",))

    def test_unknown_default_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_default_locale("klingon", ("en",))


class ValidatePseudoLocaleTests(unittest.TestCase):
    def test_zero_without_qps_ok(self):
        self.assertEqual(ec.validate_pseudo_locale("0", ("en",)), 0)

    def test_one_with_qps_ok(self):
        self.assertEqual(ec.validate_pseudo_locale("1", ("en", "qps-ploc")), 1)

    def test_one_without_qps_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_pseudo_locale("1", ("en",))

    def test_zero_with_qps_rejected(self):
        with self.assertRaises(ec.ConfigError):
            ec.validate_pseudo_locale("0", ("en", "qps-ploc"))

    def test_non_zero_one_value_rejected(self):
        for bogus in ("2", "true", "yes", "-1", ""):
            with self.assertRaises(ec.ConfigError):
                ec.validate_pseudo_locale(bogus, ("en",))


class ValidateLocaleRomSizeTests(unittest.TestCase):
    def test_english_and_pseudo_profiles_allow_16m(self):
        ec.validate_locale_rom_size(("en",), 16 * 1024 * 1024)
        ec.validate_locale_rom_size(("en", "qps-ploc"), 16 * 1024 * 1024)

    def test_real_cjk_profiles_require_32m(self):
        for locales in (
            ("en", "ja"),
            ("en", "zh-Hans"),
            ("en", "ja", "zh-Hans"),
        ):
            with self.subTest(locales=locales):
                with self.assertRaises(ec.ConfigError) as ctx:
                    ec.validate_locale_rom_size(locales, 16 * 1024 * 1024)
                self.assertIn("MODERN_ROM_SIZE=32M", str(ctx.exception))

    def test_real_cjk_profiles_allow_32m(self):
        for locales in (
            ("en", "ja"),
            ("en", "zh-Hans"),
            ("en", "ja", "zh-Hans"),
        ):
            with self.subTest(locales=locales):
                ec.validate_locale_rom_size(locales, 32 * 1024 * 1024)

    def test_real_eu_profiles_require_and_allow_32m(self):
        locales = ("en", "fr", "de", "es", "it")
        with self.assertRaises(ec.ConfigError):
            ec.validate_locale_rom_size(locales, 16 * 1024 * 1024)
        ec.validate_locale_rom_size(locales, 32 * 1024 * 1024)


class ComputeLocaleMaskTests(unittest.TestCase):
    def test_en_only_mask_is_bit_zero(self):
        self.assertEqual(ec.compute_locale_mask(("en",)), 0x1)

    def test_en_and_qps_mask_matches_bit_positions(self):
        # en=0, qps-ploc=7 -- see scripts/localization/schema.py LOCALE_IDS.
        self.assertEqual(ec.compute_locale_mask(("en", "qps-ploc")), 0x81)

    def test_en_ja_zh_mask_matches_stable_bit_positions(self):
        self.assertEqual(ec.compute_locale_mask(("en", "ja", "zh-Hans")), 0x7)

    def test_en_eu_mask_matches_stable_bit_positions(self):
        self.assertEqual(
            ec.compute_locale_mask(("en", "fr", "de", "es", "it")),
            0x79,
        )


class LoadIdentityLocaleTests(unittest.TestCase):
    """load_identity() end-to-end locale config resolution -- defaults,
    overrides, invalid combinations, and the fingerprint/save-epoch
    independence guarantee (issue #18 sprint 1 WHAT item 5)."""

    def test_defaults_are_en_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            identity = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp),
            )
        self.assertEqual(identity.enabled_locales, ("en",))
        self.assertEqual(identity.default_locale, "en")
        self.assertEqual(identity.pseudo_locale_enabled, 0)
        self.assertEqual(identity.enabled_locale_mask, 0x1)
        self.assertEqual(identity.default_locale_id, 0)

    def test_config_mk_qps_enabled_is_honored(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(
                Path(tmp), enabled_locales="en,qps-ploc", pseudo_locale="1"
            )
            identity = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp),
            )
        self.assertEqual(identity.enabled_locales, ("en", "qps-ploc"))
        self.assertEqual(identity.pseudo_locale_enabled, 1)
        self.assertEqual(identity.enabled_locale_mask, 0x81)

    def test_override_wins_over_config_mk(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            identity = ec.load_identity(
                config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp),
                enabled_locales="en,qps-ploc", default_locale="qps-ploc", pseudo_locale="1",
            )
        self.assertEqual(identity.enabled_locales, ("en", "qps-ploc"))
        self.assertEqual(identity.default_locale, "qps-ploc")
        self.assertEqual(identity.pseudo_locale_enabled, 1)

    def test_invalid_locale_combination_from_config_mk_fails_early(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp), pseudo_locale="1")  # qps not enabled
            with self.assertRaises(ec.ConfigError):
                ec.load_identity(
                    config_mk_path=config_mk, config_preset="debug", abi="aapcs",
                    rom_size="16M", repo_root=Path(tmp),
                )

    def test_cjk_profiles_reject_16m_and_resolve_at_32m(self):
        for configured in ("en,ja", "en,zh-Hans", "zh-Hans,en,ja"):
            with self.subTest(configured=configured):
                with tempfile.TemporaryDirectory() as tmp:
                    config_mk = write_config_mk(
                        Path(tmp), enabled_locales=configured
                    )
                    with self.assertRaises(ec.ConfigError) as ctx:
                        ec.load_identity(
                            config_mk_path=config_mk,
                            config_preset="debug",
                            abi="aapcs",
                            rom_size="16M",
                            repo_root=Path(tmp),
                        )
                    identity = ec.load_identity(
                        config_mk_path=config_mk,
                        config_preset="debug",
                        abi="aapcs",
                        rom_size="32M",
                        repo_root=Path(tmp),
                    )
                self.assertIn("MODERN_ROM_SIZE=32M", str(ctx.exception))
                expected = tuple(
                    locale
                    for locale in ("en", "ja", "zh-Hans")
                    if locale in configured.split(",")
                )
                self.assertEqual(identity.enabled_locales, expected)

    def test_cjk_default_locale_ja_and_zh_hans_resolve(self):
        for default_locale, expected_id in (("ja", 1), ("zh-Hans", 2)):
            with self.subTest(default_locale=default_locale):
                with tempfile.TemporaryDirectory() as tmp:
                    config_mk = write_config_mk(
                        Path(tmp),
                        enabled_locales="en,ja,zh-Hans",
                        default_locale=default_locale,
                    )
                    identity = ec.load_identity(
                        config_mk_path=config_mk,
                        config_preset="release",
                        abi="aapcs",
                        rom_size="32M",
                        repo_root=Path(tmp),
                    )
                self.assertEqual(identity.default_locale, default_locale)
                self.assertEqual(identity.default_locale_id, expected_id)
                self.assertEqual(identity.enabled_locale_mask, 0x7)

    def test_locale_config_changes_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            config_a = write_config_mk(Path(tmp_a))
            config_b = write_config_mk(
                Path(tmp_b), enabled_locales="en,qps-ploc", pseudo_locale="1"
            )
            identity_a = ec.load_identity(
                config_mk_path=config_a, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp_a),
            )
            identity_b = ec.load_identity(
                config_mk_path=config_b, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp_b),
            )
        self.assertNotEqual(identity_a.config_fingerprint, identity_b.config_fingerprint)

    def test_locale_config_change_does_not_move_save_compat_epoch(self):
        """The exact property WHAT item 5 requires: changing the locale
        config changes the fingerprint but never the save-compat epoch."""
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            config_a = write_config_mk(Path(tmp_a), save_compat_epoch="42")
            config_b = write_config_mk(
                Path(tmp_b), save_compat_epoch="42",
                enabled_locales="en,qps-ploc", pseudo_locale="1",
            )
            identity_a = ec.load_identity(
                config_mk_path=config_a, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp_a),
            )
            identity_b = ec.load_identity(
                config_mk_path=config_b, config_preset="debug", abi="aapcs",
                rom_size="16M", repo_root=Path(tmp_b),
            )
        self.assertNotEqual(identity_a.config_fingerprint, identity_b.config_fingerprint)
        self.assertEqual(identity_a.save_compat_epoch, identity_b.save_compat_epoch)
        self.assertEqual(identity_a.save_compat_epoch, 42)


class GenerateMetadataFilesTests(unittest.TestCase):
    def _identity(self, tmp):
        config_mk = write_config_mk(Path(tmp))
        return ec.load_identity(
            config_mk_path=config_mk, config_preset="debug", abi="aapcs",
            rom_size="16M", repo_root=Path(tmp),
        )

    def test_writes_json_and_mk_with_expected_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            identity = self._identity(tmp)
            out_dir = Path(tmp) / "generated"
            paths = ec.generate_metadata_files(out_dir, identity)
            data = json.loads(paths["json"].read_text(encoding="utf-8"))
            mk_text = paths["mk"].read_text(encoding="utf-8")
        self.assertEqual(data["rom_title"], "FIREEMBLEM2E")
        self.assertEqual(data["config_fingerprint"], identity.config_fingerprint)
        self.assertIn(f"MODERN_BUILD_COMMIT := {identity.build_commit}", mk_text)
        self.assertIn(f"MODERN_CONFIG_FINGERPRINT := {identity.config_fingerprint}", mk_text)

    def test_does_not_rewrite_file_when_content_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            identity = self._identity(tmp)
            out_dir = Path(tmp) / "generated"
            paths = ec.generate_metadata_files(out_dir, identity)
            first_mtime = paths["json"].stat().st_mtime_ns
            # Regenerate with the exact same identity -- content is
            # byte-identical, so the file must not be rewritten.
            ec.generate_metadata_files(out_dir, identity)
            second_mtime = paths["json"].stat().st_mtime_ns
        self.assertEqual(first_mtime, second_mtime)

    def test_rewrites_file_when_content_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            identity = self._identity(tmp)
            out_dir = Path(tmp) / "generated"
            paths = ec.generate_metadata_files(out_dir, identity)
            original = paths["json"].read_text(encoding="utf-8")

            identity.rom_revision = 9
            ec.generate_metadata_files(out_dir, identity)
            updated = paths["json"].read_text(encoding="utf-8")
        self.assertNotEqual(original, updated)


class CliTests(unittest.TestCase):
    SCRIPT = ROOT / "scripts" / "modernize" / "expansion_config.py"

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(self.SCRIPT), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_validate_succeeds_silently_for_valid_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            result = self.run_cli(
                "validate",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_resolve_prints_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            result = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MODERN_BUILD_COMMIT=", result.stdout)
        self.assertIn("MODERN_CONFIG_FINGERPRINT=", result.stdout)
        self.assertIn("MODERN_VERSION_STRING=0.1.0", result.stdout)
        self.assertIn("MODERN_SAVE_COMPAT_EPOCH=1", result.stdout)

    def test_resolve_save_compat_epoch_override_changes_token_not_fingerprint(
        self,
    ):
        """Regression test for issue #2 slice 1 review finding #4 (HIGH):
        modern.mk threads --save-compat-epoch through this CLI's resolve
        command to get the MODERN_SAVE_COMPAT_EPOCH token it embeds in
        MODERN_CFLAGS's -D define and in the compile-settings stamp. This
        proves the override changes that token while leaving
        MODERN_CONFIG_FINGERPRINT untouched -- the save-compat epoch must
        remain a distinct compatibility gate, never folded into the #8
        config fingerprint (see ExpansionIdentity.fingerprint_fields)."""
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            baseline = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
            )
            overridden = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--save-compat-epoch", "2",
            )
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.assertEqual(overridden.returncode, 0, overridden.stderr)
        self.assertIn("MODERN_SAVE_COMPAT_EPOCH=1", baseline.stdout)
        self.assertIn("MODERN_SAVE_COMPAT_EPOCH=2", overridden.stdout)

        def fingerprint_of(stdout: str) -> str:
            for token in stdout.split():
                if token.startswith("MODERN_CONFIG_FINGERPRINT="):
                    return token
            raise AssertionError(f"no fingerprint token in: {stdout}")

        self.assertEqual(
            fingerprint_of(baseline.stdout), fingerprint_of(overridden.stdout)
        )

    def test_resolve_prints_locale_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            result = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MODERN_EXPANSION_ENABLED_LOCALE_MASK=1", result.stdout)
        self.assertIn("MODERN_EXPANSION_DEFAULT_LOCALE_ID=0", result.stdout)
        self.assertIn("MODERN_EXPANSION_PSEUDO_LOCALE_ENABLED=0", result.stdout)

    def test_resolve_locale_overrides_change_tokens_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            baseline = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
            )
            overridden = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--enabled-locales", "en,qps-ploc",
                "--pseudo-locale", "1",
            )
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.assertEqual(overridden.returncode, 0, overridden.stderr)
        self.assertIn("MODERN_EXPANSION_ENABLED_LOCALE_MASK=1 ", baseline.stdout)
        self.assertIn("MODERN_EXPANSION_ENABLED_LOCALE_MASK=129", overridden.stdout)

        def fingerprint_of(stdout: str) -> str:
            for token in stdout.split():
                if token.startswith("MODERN_CONFIG_FINGERPRINT="):
                    return token
            raise AssertionError(f"no fingerprint token in: {stdout}")

        self.assertNotEqual(fingerprint_of(baseline.stdout), fingerprint_of(overridden.stdout))

    def test_resolve_fails_early_on_inconsistent_locale_combination(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            result = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--pseudo-locale", "1",  # qps-ploc not enabled -- must fail
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("error:", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_resolve_cjk_profiles_require_32m_and_emit_real_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            rejected = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--enabled-locales", "en,ja",
            )
            accepted = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "32M",
                "--repo-root", tmp,
                "--enabled-locales", "zh-Hans,en,ja",
                "--default-locale", "zh-Hans",
            )
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("MODERN_ROM_SIZE=32M", rejected.stderr)
        self.assertEqual(rejected.stdout, "")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("MODERN_EXPANSION_ENABLED_LOCALE_MASK=7", accepted.stdout)
        self.assertIn("MODERN_EXPANSION_DEFAULT_LOCALE_ID=2", accepted.stdout)

    def test_generate_writes_metadata_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            out_dir = Path(tmp) / "generated"
            result = self.run_cli(
                "generate",
                "--config-mk", str(config_mk),
                "--config", "release",
                "--abi", "apcs-gnu",
                "--rom-size", "32M",
                "--repo-root", tmp,
                "--output-dir", str(out_dir),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads((out_dir / "expansion_build_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(data["config_preset"], "release")
            self.assertEqual(data["abi"], "apcs-gnu")
            self.assertEqual(data["rom_size_bytes"], 32 * 1024 * 1024)

    def test_resolve_honors_identity_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            result = self.run_cli(
                "resolve",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--title", "FE8CUSTOMTST",
                "--game-code", "ZZZZ",
                "--maker-code", "9X",
                "--revision", "7",
            )
            out_dir = Path(tmp) / "generated"
            self.run_cli(
                "generate",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--output-dir", str(out_dir),
                "--title", "FE8CUSTOMTST",
                "--game-code", "ZZZZ",
                "--maker-code", "9X",
                "--revision", "7",
            )
            data = json.loads(
                (out_dir / "expansion_build_metadata.json").read_text(encoding="utf-8")
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["rom_title"], "FE8CUSTOMTST")
        self.assertEqual(data["rom_game_code"], "ZZZZ")
        self.assertEqual(data["rom_maker_code"], "9X")
        self.assertEqual(data["rom_revision"], 7)

    # -- invalid inputs / incompatible combinations fail before any write ---

    def test_invalid_title_fails_before_writing_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp), rom_title="TOOLONGTITLE!!")
            out_dir = Path(tmp) / "generated"
            result = self.run_cli(
                "generate",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--output-dir", str(out_dir),
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EXPANSION_ROM_TITLE", result.stderr)
        self.assertFalse(out_dir.exists())

    def test_invalid_game_code_fails_before_writing_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp), rom_game_code="ABC")
            out_dir = Path(tmp) / "generated"
            result = self.run_cli(
                "generate",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--output-dir", str(out_dir),
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EXPANSION_ROM_GAME_CODE", result.stderr)
        self.assertFalse(out_dir.exists())

    def test_unsupported_preset_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            result = self.run_cli(
                "validate",
                "--config-mk", str(config_mk),
                "--config", "profiling",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error", result.stderr)

    def test_unsupported_abi_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            result = self.run_cli(
                "validate",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "eabi",
                "--rom-size", "16M",
                "--repo-root", tmp,
            )
        self.assertNotEqual(result.returncode, 0)
        # argparse enforces the choices= constraint itself (SUPPORTED_ABIS),
        # so an unsupported --abi is rejected before validate_abi() even
        # runs -- still an actionable, pre-compilation diagnostic.
        self.assertIn("--abi", result.stderr)
        self.assertIn("invalid choice", result.stderr)

    def test_unsupported_rom_size_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            result = self.run_cli(
                "validate",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "8M",
                "--repo-root", tmp,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MODERN_ROM_SIZE", result.stderr)

    def test_invalid_build_id_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            result = self.run_cli(
                "validate",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--build-id", "not-a-sha-branch-name",
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EXPANSION_BUILD_ID", result.stderr)

    def test_missing_config_mk_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "validate",
                "--config-mk", str(Path(tmp) / "does-not-exist.mk"),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("config.mk", result.stderr)


class ValidateFeatureFlagTests(unittest.TestCase):
    """Issue #6: each starter-feature flag is a strict 0/1 build switch."""

    def test_zero_and_one_pass_through(self):
        self.assertEqual(ec.validate_feature_flag("EXPANSION_MECHANICS_HOOKS", "0"), 0)
        self.assertEqual(ec.validate_feature_flag("EXPANSION_MECHANICS_HOOKS", "1"), 1)
        self.assertEqual(ec.validate_feature_flag("EXPANSION_MECHANICS_HOOKS", 1), 1)

    def test_negative_one_rejected(self):
        with self.assertRaises(ec.ConfigError) as ctx:
            ec.validate_feature_flag("EXPANSION_MECHANICS_HOOKS", "-1")
        self.assertIn("EXPANSION_MECHANICS_HOOKS", str(ctx.exception))

    def test_two_rejected(self):
        with self.assertRaises(ec.ConfigError) as ctx:
            ec.validate_feature_flag("EXPANSION_DANGER_OVERLAY_MENU", "2")
        self.assertIn("out of range", str(ctx.exception))

    def test_text_rejected(self):
        with self.assertRaises(ec.ConfigError) as ctx:
            ec.validate_feature_flag("EXPANSION_MECHANICS_SAMPLE", "yes")
        self.assertIn("not an integer", str(ctx.exception))


class ValidateFeatureFlagRelationshipTests(unittest.TestCase):
    """Issue #6: the sample mechanic can only exist with the hook registry."""

    def test_sample_requires_hooks(self):
        with self.assertRaises(ec.ConfigError) as ctx:
            ec.validate_feature_flags("0", "1", "0")
        message = str(ctx.exception)
        self.assertIn("EXPANSION_MECHANICS_SAMPLE=1", message)
        self.assertIn("EXPANSION_MECHANICS_HOOKS=1", message)

    def test_sample_with_hooks_is_ok(self):
        self.assertEqual(ec.validate_feature_flags("1", "1", "0"), (1, 1, 0, 0))

    def test_all_off_is_ok(self):
        self.assertEqual(ec.validate_feature_flags("0", "0", "0"), (0, 0, 0, 0))

    def test_hooks_without_sample_is_ok(self):
        self.assertEqual(ec.validate_feature_flags("1", "0", "1"), (1, 0, 1, 0))

    def test_content_defaults_off_for_legacy_three_argument_callers(self):
        """The issue #6 Sprint 2 content flag is an OPTIONAL fourth switch:
        an existing three-argument call keeps its exact previous meaning and
        simply resolves the content flag to 0."""
        self.assertEqual(ec.validate_feature_flags("1", "1", "1"), (1, 1, 1, 0))


class LoadIdentityFeatureFlagTests(unittest.TestCase):
    """Issue #6: flags flow into the identity/JSON and the fingerprint, but
    never into the independent save-compatibility epoch/key."""

    def _identity(self, tmp, **kwargs):
        config_mk = write_config_mk(Path(tmp))
        return ec.load_identity(
            config_mk_path=config_mk,
            config_preset="debug",
            abi="aapcs",
            rom_size="16M",
            repo_root=Path(tmp),
            **kwargs,
        )

    def test_absent_flags_default_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            identity = self._identity(tmp)
        self.assertEqual(identity.mechanics_hooks, 0)
        self.assertEqual(identity.mechanics_sample, 0)
        self.assertEqual(identity.danger_overlay_menu, 0)
        self.assertEqual(identity.blue_phase_delegate, 0)

    def test_flags_appear_in_json_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            identity = self._identity(
                tmp,
                mechanics_hooks="1",
                danger_overlay_menu="1",
                blue_phase_delegate="1",
            )
        data = identity.to_dict()
        self.assertEqual(data["mechanics_hooks"], 1)
        self.assertEqual(data["mechanics_sample"], 0)
        self.assertEqual(data["danger_overlay_menu"], 1)
        self.assertEqual(data["blue_phase_delegate"], 1)

    def test_autoplay_profile_flag_changes_identity_without_save_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._identity(tmp)
            enabled = self._identity(tmp, autoplay_strategies="1")
            configured = ec.load_identity(
                config_mk_path=write_config_mk(Path(tmp), autoplay_strategies="1"),
                config_preset="debug",
                abi="aapcs",
                rom_size="16M",
                repo_root=Path(tmp),
            )
        self.assertEqual(base.autoplay_strategies, 0)
        self.assertEqual(enabled.autoplay_strategies, 1)
        self.assertEqual(configured.autoplay_strategies, 1)
        self.assertNotEqual(base.config_fingerprint, enabled.config_fingerprint)
        self.assertEqual(base.save_compat_epoch, enabled.save_compat_epoch)
        with self.assertRaises(ec.ConfigError):
            ec.validate_feature_flag("EXPANSION_AUTOPLAY_STRATEGIES", "2")

    def test_flag_change_changes_fingerprint_but_not_epoch_or_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._identity(tmp)
            hooks = self._identity(tmp, mechanics_hooks="1")
            sample = self._identity(tmp, mechanics_hooks="1", mechanics_sample="1")
            danger = self._identity(tmp, danger_overlay_menu="1")
            delegate = self._identity(tmp, blue_phase_delegate="1")
        # Every flag toggle changes the config fingerprint...
        self.assertNotEqual(base.config_fingerprint, hooks.config_fingerprint)
        self.assertNotEqual(hooks.config_fingerprint, sample.config_fingerprint)
        self.assertNotEqual(base.config_fingerprint, danger.config_fingerprint)
        self.assertNotEqual(base.config_fingerprint, delegate.config_fingerprint)
        # ...while the save-compatibility epoch/key is untouched by all of them.
        for identity in (base, hooks, sample, danger, delegate):
            self.assertEqual(identity.save_compat_epoch, 1)

    def test_feature_flags_are_not_in_save_compat_key(self):
        """The save-compatibility gate is the epoch alone; flags live only in
        the diagnostic fingerprint, so they can never make an existing save
        look incompatible."""
        with tempfile.TemporaryDirectory() as tmp:
            base = self._identity(tmp)
            hooks = self._identity(tmp, mechanics_hooks="1")
        self.assertEqual(base.save_compat_epoch, hooks.save_compat_epoch)
        self.assertIn("features", base.fingerprint_fields())
        self.assertNotIn("save_compat_epoch", base.fingerprint_fields())

    def test_fingerprint_features_cover_all_parsed_feature_config_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            default = self._identity(tmp)
            custom_spell = self._identity(
                tmp,
                custom_spell_effects="1",
                asset_manifest=(
                    ROOT / "assets" / "manifests"
                    / "custom-spell-reference.json"
                ),
            )

        expected = {
            key.removeprefix("EXPANSION_").lower()
            for key in ec.CONFIG_MK_FEATURE_KEYS
        }
        actual = (
            set(default.fingerprint_fields()["features"])
            | set(custom_spell.fingerprint_fields()["features"])
            | {"bgm_continuation_policy"}
        )
        self.assertEqual(actual, expected)
        self.assertIn("bgm_continuation_policy", default.fingerprint_fields())

    def test_custom_spell_default_preserves_pre_feature_fingerprint_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            implicit_default = self._identity(tmp)
            explicit_default = self._identity(tmp, custom_spell_effects="0")
            enabled = self._identity(
                tmp,
                custom_spell_effects="1",
                asset_manifest=(
                    ROOT / "assets" / "manifests"
                    / "custom-spell-reference.json"
                ),
            )

        self.assertEqual(
            implicit_default.fingerprint_fields(),
            explicit_default.fingerprint_fields(),
        )
        self.assertEqual(
            implicit_default.config_fingerprint,
            explicit_default.config_fingerprint,
        )
        self.assertNotIn(
            "custom_spell_effects",
            implicit_default.fingerprint_fields()["features"],
        )
        self.assertNotIn(
            "custom_spell_effect_contract",
            implicit_default.fingerprint_fields(),
        )
        self.assertEqual(
            enabled.fingerprint_fields()["features"]["custom_spell_effects"],
            1,
        )
        self.assertIn("custom_spell_effect_contract", enabled.fingerprint_fields())
        self.assertNotEqual(
            implicit_default.config_fingerprint,
            enabled.config_fingerprint,
        )

    def test_custom_spell_inventory_digest_changes_fingerprint(self):
        original = {
            "inventory_digest": "1" * 64,
            "resource_digest": "2" * 64,
        }
        moved_sprite = {
            "inventory_digest": "3" * 64,
            "resource_digest": original["resource_digest"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                ec,
                "resolve_custom_spell_contract",
                side_effect=(original, moved_sprite),
            ):
                original_identity = self._identity(
                    tmp,
                    custom_spell_effects="1",
                    asset_manifest="unused.json",
                )
                moved_identity = self._identity(
                    tmp,
                    custom_spell_effects="1",
                    asset_manifest="unused.json",
                )

        self.assertNotEqual(
            original_identity.custom_spell_effect_inventory_digest,
            moved_identity.custom_spell_effect_inventory_digest,
        )
        self.assertEqual(
            original_identity.custom_spell_effect_resource_budget_digest,
            moved_identity.custom_spell_effect_resource_budget_digest,
        )
        self.assertNotEqual(
            original_identity.config_fingerprint, moved_identity.config_fingerprint
        )
        self.assertEqual(
            original_identity.save_compat_epoch, moved_identity.save_compat_epoch
        )

    def test_custom_spell_uses_resolved_item_cap_not_environment(self):
        contract = {
            "inventory_digest": "1" * 64,
            "resource_digest": "2" * 64,
        }
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.dict(
                    "os.environ", {"FE8_ITEM_ID_CAP": "0xCD"}, clear=False
                ),
                mock.patch.object(
                    ec,
                    "resolve_custom_spell_contract",
                    return_value=contract,
                ) as resolver,
            ):
                identity = self._identity(
                    tmp,
                    custom_spell_effects="1",
                    asset_manifest="unused.json",
                    item_id_cap="0xCE",
                )
        self.assertEqual(identity.item_id_cap, 0xCE)
        resolver.assert_called_once_with(
            Path(tmp), "unused.json", 1, 0xCE
        )

    def test_sample_without_hooks_rejected_in_load_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ec.ConfigError):
                self._identity(tmp, mechanics_sample="1")


class FeatureFlagCliTests(unittest.TestCase):
    SCRIPT = ROOT / "scripts" / "modernize" / "expansion_config.py"

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(self.SCRIPT), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def _resolve(self, tmp, *extra):
        config_mk = write_config_mk(Path(tmp))
        return self.run_cli(
            "resolve",
            "--config-mk", str(config_mk),
            "--config", "debug",
            "--abi", "aapcs",
            "--rom-size", "16M",
            "--repo-root", tmp,
            *extra,
        )

    @staticmethod
    def _fingerprint_of(stdout):
        for token in stdout.split():
            if token.startswith("MODERN_CONFIG_FINGERPRINT="):
                return token
        raise AssertionError(f"no fingerprint token in: {stdout}")

    def test_hooks_flag_changes_fingerprint_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline = self._resolve(tmp)
            hooks = self._resolve(tmp, "--mechanics-hooks", "1")
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.assertEqual(hooks.returncode, 0, hooks.stderr)
        self.assertNotEqual(
            self._fingerprint_of(baseline.stdout), self._fingerprint_of(hooks.stdout)
        )
        # The epoch token is unchanged by a feature-flag toggle.
        self.assertIn("MODERN_SAVE_COMPAT_EPOCH=1", baseline.stdout)
        self.assertIn("MODERN_SAVE_COMPAT_EPOCH=1", hooks.stdout)

    def test_sample_without_hooks_fails_at_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._resolve(tmp, "--mechanics-sample", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EXPANSION_MECHANICS_SAMPLE=1", result.stderr)

    def test_invalid_flag_value_fails_at_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._resolve(tmp, "--blue-phase-delegate", "2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("out of range", result.stderr)
        self.assertIn("EXPANSION_BLUE_PHASE_DELEGATE", result.stderr)

    def test_generate_json_contains_feature_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_mk = write_config_mk(Path(tmp))
            out_dir = Path(tmp) / "generated"
            result = self.run_cli(
                "generate",
                "--config-mk", str(config_mk),
                "--config", "debug",
                "--abi", "aapcs",
                "--rom-size", "16M",
                "--repo-root", tmp,
                "--mechanics-hooks", "1",
                "--danger-overlay-menu", "1",
                "--blue-phase-delegate", "1",
                "--output-dir", str(out_dir),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(
                (out_dir / "expansion_build_metadata.json").read_text(encoding="utf-8")
            )
        self.assertEqual(data["mechanics_hooks"], 1)
        self.assertEqual(data["mechanics_sample"], 0)
        self.assertEqual(data["danger_overlay_menu"], 1)
        self.assertEqual(data["blue_phase_delegate"], 1)


class BluePhaseDelegateFlagTests(unittest.TestCase):
    def _identity(self, **kwargs):
        params = dict(
            config_mk_path=ROOT / "config.mk",
            config_preset="release",
            abi="aapcs",
            rom_size="16M",
            build_id_override="abcdef12",
        )
        params.update(kwargs)
        return ec.load_identity(**params)

    def test_default_is_off_and_strict(self):
        identity = self._identity()
        self.assertEqual(identity.blue_phase_delegate, 0)
        self.assertEqual(
            identity.fingerprint_fields()["features"]["blue_phase_delegate"], 0
        )
        for bad in ("-1", "2", "yes"):
            with self.subTest(value=bad):
                with self.assertRaises(ec.ConfigError) as ctx:
                    self._identity(blue_phase_delegate=bad)
                self.assertIn("EXPANSION_BLUE_PHASE_DELEGATE", str(ctx.exception))

    def test_toggle_changes_identity_but_not_save_compatibility(self):
        disabled = self._identity(blue_phase_delegate=0)
        enabled = self._identity(blue_phase_delegate=1)
        self.assertNotEqual(disabled.config_fingerprint, enabled.config_fingerprint)
        self.assertEqual(disabled.save_compat_epoch, enabled.save_compat_epoch)

    def test_c_and_make_contracts_are_wired(self):
        header = (ROOT / "include" / "expansion_config.h").read_text(
            encoding="utf-8"
        )
        modern_mk = (ROOT / "modern.mk").read_text(encoding="utf-8")
        self.assertIn("#define FE8_EXPANSION_BLUE_PHASE_DELEGATE 0", header)
        self.assertIn(
            "FE8_EXPANSION_BLUE_PHASE_DELEGATE must be 0 or 1", header
        )
        self.assertIn(
            "requires the modern issue #85 blue controller", header
        )
        self.assertIn(
            "-DFE8_EXPANSION_BLUE_PHASE_DELEGATE=$(EXPANSION_BLUE_PHASE_DELEGATE)",
            modern_mk,
        )
        self.assertIn(
            '--blue-phase-delegate "$(EXPANSION_BLUE_PHASE_DELEGATE)"',
            modern_mk,
        )
        self.assertIn(
            "blue_phase_delegate=$(EXPANSION_BLUE_PHASE_DELEGATE)", modern_mk
        )


class StarterContentFlagTests(unittest.TestCase):
    """Issue #6 Sprint 2: the EXPANSION_STARTER_CONTENT opt-in flag.

    An independent 0/1 identity flag with two hard dependencies -- the
    mechanics hook registry, and an item ID cap that actually reaches
    ITEM_EXPANSION_CE -- folded into the config fingerprint and the
    generated metadata, and deliberately NOT part of the save format.
    """

    def _identity(self, **kwargs):
        params = dict(
            config_mk_path=ROOT / "config.mk",
            config_preset="release",
            abi="aapcs",
            rom_size="16M",
            build_id_override="abcdef12",
        )
        params.update(kwargs)
        return ec.load_identity(**params)

    def test_default_is_off(self):
        identity = self._identity()
        self.assertEqual(identity.starter_content, 0)
        self.assertEqual(
            identity.fingerprint_fields()["features"]["starter_content"], 0)

    def test_config_mk_default_is_zero(self):
        cfg = ec.parse_config_mk(ROOT / "config.mk")
        self.assertEqual(cfg["EXPANSION_STARTER_CONTENT"], "0")

    def test_enabled_requires_mechanics_hooks(self):
        with self.assertRaises(ec.ConfigError) as ctx:
            self._identity(starter_content=1, mechanics_hooks=0,
                           item_id_cap="0xCE")
        message = str(ctx.exception)
        self.assertIn("EXPANSION_STARTER_CONTENT=1", message)
        self.assertIn("EXPANSION_MECHANICS_HOOKS=1", message)

    def test_enabled_requires_expanded_item_cap(self):
        with self.assertRaises(ec.ConfigError) as ctx:
            self._identity(starter_content=1, mechanics_hooks=1)
        message = str(ctx.exception)
        self.assertIn("EXPANSION_STARTER_CONTENT=1", message)
        self.assertIn("FE8_ITEM_ID_CAP", message)
        self.assertIn("ITEM_EXPANSION_CE", message)

    def test_enabled_with_both_dependencies_resolves(self):
        identity = self._identity(starter_content=1, mechanics_hooks=1,
                                  item_id_cap="0xCE")
        self.assertEqual(identity.starter_content, 1)

    def test_invalid_values_rejected_actionably(self):
        for bad in ("2", "-1", "yes", ""):
            if bad == "":
                continue
            with self.assertRaises(ec.ConfigError) as ctx:
                self._identity(starter_content=bad, mechanics_hooks=1,
                               item_id_cap="0xCE")
            self.assertIn("EXPANSION_STARTER_CONTENT", str(ctx.exception))

    def test_platform_stays_testable_at_any_cap_with_content_off(self):
        """The dependency is one-way: raising the cap alone must never
        require the content flag, so the issue #10 ID-space platform stays
        independently testable."""
        for cap in (None, "0xCD", "0xCE", "0xFF"):
            identity = self._identity(starter_content=0, item_id_cap=cap)
            self.assertEqual(identity.starter_content, 0)

    def test_flag_changes_the_config_fingerprint(self):
        off = self._identity(starter_content=0, mechanics_hooks=1,
                             item_id_cap="0xCE")
        on = self._identity(starter_content=1, mechanics_hooks=1,
                            item_id_cap="0xCE")
        self.assertNotEqual(off.config_fingerprint, on.config_fingerprint)

    def test_flag_never_changes_the_save_compat_epoch(self):
        off = self._identity(starter_content=0, mechanics_hooks=1,
                             item_id_cap="0xCE")
        on = self._identity(starter_content=1, mechanics_hooks=1,
                            item_id_cap="0xCE")
        self.assertEqual(off.save_compat_epoch, on.save_compat_epoch)
        # Compared against the real, committed config.mk's current epoch
        # (issue #18 sprint 2 bumped it 1 -> 2) rather than a hardcoded
        # literal, so this test never silently drifts from that file again.
        cfg = ec.parse_config_mk(ROOT / "config.mk")
        self.assertEqual(
            on.save_compat_epoch, int(cfg["EXPANSION_SAVE_COMPAT_EPOCH"])
        )

    def test_flag_appears_in_generated_metadata_json(self):
        identity = self._identity(starter_content=1, mechanics_hooks=1,
                                  item_id_cap="0xCE")
        with tempfile.TemporaryDirectory() as tmp:
            paths = ec.generate_metadata_files(Path(tmp), identity)
            data = json.loads(paths["json"].read_text(encoding="utf-8"))
        self.assertEqual(data["starter_content"], 1)

    def test_item_cap_constants_match_the_idspace_source_of_truth(self):
        """expansion_config.py restates the item cap boundary because it runs
        as a bare script; it must never drift from idspace.py, which owns it."""
        sys.path.insert(0, str(ROOT))
        from scripts.generated_data import idspace

        self.assertEqual(ec.ITEM_ID_EXPANSION_FIRST, idspace.ITEM_EXPANSION_FIRST)
        self.assertEqual(ec.ITEM_ID_DEFAULT_CAP, idspace.ITEM_DEFAULT_CAP)
        self.assertEqual(ec.ITEM_ID_DEFAULT_CAP,
                         idspace.domain_by_key("item").configured_cap)

    def test_invalid_item_cap_rejected(self):
        with self.assertRaises(ec.ConfigError):
            self._identity(item_id_cap="not-a-number")
        with self.assertRaises(ec.ConfigError):
            self._identity(item_id_cap="0x100")


class StarterContentCompileTimeContractTests(unittest.TestCase):
    """The same two dependencies must also be hard C compile errors."""

    @staticmethod
    def preprocess(header, *defines):
        compiler = shutil.which("arm-none-eabi-cpp")
        driver_args = []
        if compiler is None:
            compiler = shutil.which("arm-none-eabi-gcc")
            driver_args = ["-E"]
        if compiler is None:
            raise unittest.SkipTest("arm-none-eabi-cpp/-gcc is not available")
        return subprocess.run(
            [
                compiler,
                *driver_args,
                "-dM",
                "-I",
                str(ROOT / "include"),
                *(f"-D{definition}" for definition in defines),
                "-x",
                "c",
                "-",
            ],
            input=f'#include "{header}"\n',
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    @mock.patch("subprocess.run")
    @mock.patch("shutil.which")
    def test_preprocess_falls_back_to_gcc_driver(self, which, run):
        which.side_effect = lambda name: (
            "/toolchain/arm-none-eabi-gcc"
            if name == "arm-none-eabi-gcc"
            else None
        )

        self.preprocess("expansion_config.h")

        self.assertEqual(
            which.call_args_list,
            [
                mock.call("arm-none-eabi-cpp"),
                mock.call("arm-none-eabi-gcc"),
            ],
        )
        command = run.call_args.args[0]
        self.assertEqual(command[0], "/toolchain/arm-none-eabi-gcc")
        self.assertIn("-E", command)
        self.assertIn("-dM", command)

    @staticmethod
    def macros(output):
        values = {}
        for line in output.splitlines():
            if not line.startswith("#define "):
                continue
            definition = line[len("#define "):].split(maxsplit=1)
            name = definition[0]
            values[name] = definition[1] if len(definition) == 2 else ""
        return values

    def test_macros_accepts_valued_and_value_less_definitions(self):
        fixture = "\n".join(
            (
                "#define FE8_EXPANSION_STARTER_CONTENT 0",
                "#define CPP_IMPLEMENTATION",
            )
        )
        values = self.macros(fixture)
        self.assertEqual(values["FE8_EXPANSION_STARTER_CONTENT"], "0")
        self.assertEqual(values["CPP_IMPLEMENTATION"], "")

    def test_macros_treats_a_value_less_mutation_as_empty(self):
        fixture = "#define FE8_EXPANSION_STARTER_CONTENT 1"
        mutated = fixture.replace(" 1", "")
        self.assertEqual(
            self.macros(fixture)["FE8_EXPANSION_STARTER_CONTENT"],
            "1",
        )
        self.assertEqual(
            self.macros(mutated)["FE8_EXPANSION_STARTER_CONTENT"],
            "",
        )

    def test_config_header_defaults_the_flag_off(self):
        result = self.preprocess("expansion_config.h")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.macros(result.stdout)["FE8_EXPANSION_STARTER_CONTENT"],
            "0",
        )

    def test_config_header_errors_without_hooks(self):
        result = self.preprocess(
            "expansion_config.h",
            "FE8_EXPANSION_STARTER_CONTENT=1",
            "FE8_EXPANSION_MECHANICS_HOOKS=0",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires FE8_EXPANSION_MECHANICS_HOOKS=1", result.stderr)

    def test_content_header_errors_below_the_expansion_cap(self):
        result = self.preprocess(
            "expansion_starter_content.h",
            "FE8_EXPANSION_STARTER_CONTENT=1",
            "FE8_EXPANSION_MECHANICS_HOOKS=1",
            "FE8_ITEM_ID_CAP=0xCD",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires an expanded item cap", result.stderr)

    def test_modern_mk_flows_the_flag_and_cap(self):
        result = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-rR",
                "-n",
                "-p",
                "__issue102_config_probe__",
                "MODERN_CONFIG_RESOLVE_GOALS=__issue102_config_probe__",
                "EXPANSION_STARTER_CONTENT=1",
                "EXPANSION_MECHANICS_HOOKS=1",
                "FE8_ITEM_ID_CAP=0xCE",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        cflags = make_database_variable(result.stdout, "MODERN_CFLAGS")
        self.assertIsNotNone(cflags, result.stdout[-4000:])
        self.assertIn("-DFE8_EXPANSION_STARTER_CONTENT=1", cflags)
        self.assertIn("-DFE8_ITEM_ID_CAP=0xCE", cflags)

    def test_make_database_variable_collects_cflags_continuations(self):
        fixture = "\n".join(
            (
                "MODERN_CFLAGS := -DFE8_EXPANSION_STARTER_CONTENT=1 \\",
                "    -DFE8_ITEM_ID_CAP=0xCE",
                "    recipe-must-not-be-part-of-the-variable",
                "# automatic",
            )
        )
        cflags = make_database_variable(fixture, "MODERN_CFLAGS")
        self.assertIn("-DFE8_EXPANSION_STARTER_CONTENT=1", cflags)
        self.assertIn("-DFE8_ITEM_ID_CAP=0xCE", cflags)


if __name__ == "__main__":
    unittest.main()
