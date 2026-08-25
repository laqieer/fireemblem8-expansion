# GBA playtest semantic evidence

Issue [#156](https://github.com/laqieer/fireemblem8-expansion/issues/156)
owns five `gba-playtest-runtime` audit records from parent issue #105. It
replaces source-spelling and source-order evidence with parsed AST, real Make
dry-run, and optimized ARM-object behavior. No gameplay, save, generated-data,
localization, ROM/RAM, configuration, default, release, or archival behavior
changes.

| Audit ID | Replacement evidence | Positive control | Adversarial control |
| --- | --- | --- | --- |
| `test_host_only_mode.py::HostOnlyClassificationTests.test_repository_rom_paths_are_only_built_in_host_mode` | Parsed AST tracks repository-rooted ROM/ELF construction and file access. | All production test modules keep repository artifacts centralized in `host_mode.py`. | A fixture using `REPO_ROOT` joins, `Path`, `stat`, and `read_bytes` is discovered. |
| `test_host_only_mode.py::HostOnlyClassificationTests.test_modules_using_repository_roms_are_registered_as_live` | Parsed AST tracks qualified host-mode APIs/attributes and repository-backed direct live capture owners. | Every discovered production owner is registered and guarded before host-only artifact access. | An unregistered fixture that constructs a repository ROM and calls `gba_playtest.capture` fails closed. |
| `test_probe_bindings.py::ProbeBindingToolTests.test_make_threads_modern_nm_to_playtest_and_binding_tools` | Real `make -n` starter target output is parsed as command options for debug/release. | Every emitted binding command carries the configured `MODERN_NM` and complete ELF/scenario/fingerprint inputs. | Replacing the configured symbol tool would change the parsed option and fail. |
| `test_worldmap_proc_iter_null_guard.py::ProcFindNextSourceGuardTests.test_named_helpers_contain_the_guard` | Stable audit ID now discovers every optimized world-map `Proc_FindNext` relocation. | The release object set exposes one or more iterator relocations. | Removing all iterator use fails the compiled discovery. |
| `test_worldmap_proc_iter_null_guard.py::ProcFindNextCodegenTests.test_release_build_can_still_leave_the_iterator_loop` | Every optimized world-map `Proc_FindNext` relocation is paired with a null-control-flow branch in the actual ARM object. | All generated release iterator calls retain exhausted-list behavior. | Restoring a pre-null-dereference loses the branch and fails. |

`TC-TEST-QUALITY-001` governs this work: semantic-preserving formatting and
renaming remain valid, while artifact-boundary, symbol-tool forwarding, or
release iterator behavior mutations fail. The existing default/release
negative controls remain the authority for runtime behavior; no scenario
fingerprint is refreshed by this issue.

## Scope and rollback

This independent root depends only on merged #100 and has no PR dependency.
It has no feature/profile, save, generated-data, localization, or resource
conflict. Reverting this issue restores only the five audit checks and this
evidence map.
