# GBA playtest semantic evidence

Issue [#156](https://github.com/laqieer/fireemblem8-expansion/issues/156)
owns five `gba-playtest-runtime` audit records from parent issue #105. It
replaces source-spelling and source-order evidence with parsed AST, real Make
dry-run, and optimized ARM-object behavior. No gameplay, save, generated-data,
localization, ROM/RAM, configuration, default, release, or archival behavior
changes.

| Audit ID | Replacement evidence | Positive control | Adversarial control |
| --- | --- | --- | --- |
| `test_host_only_mode.py::HostOnlyClassificationTests.test_repository_rom_paths_are_only_built_in_host_mode` | Parsed AST canonicalizes `import`/`from` host-mode aliases and tracks repository-rooted ROM/ELF construction and file access. | All production test modules keep repository artifacts centralized in `host_mode.py`. | Alias, `REPO_ROOT` join, `Path`, `stat`, and `read_bytes` fixtures are discovered. |
| `test_host_only_mode.py::HostOnlyClassificationTests.test_modules_using_repository_roms_are_registered_as_live` | Parsed AST classifies central APIs, dynamic environment ROM/ELF paths, and direct capture owners; only generated temporary homebrew fixtures are excluded. | Every discovered production owner, including `StarterHookRuntimeTests`, is registered and guarded before host-only artifact access. | Unregistered direct and environment capture fixtures fail closed. |
| `test_probe_bindings.py::ProbeBindingToolTests.test_make_threads_modern_nm_to_playtest_and_binding_tools` | Real `make -n` starter target output is parsed as command options for debug/release. | Each target emits exactly two binding and two `gba_playtest.py verify` commands with configured `MODERN_NM`, positive/negative ELF, scenario, fingerprint, and probe-symbol inputs. | Deleting an invocation or its symbol-tool argument changes the parsed command set and fails. |
| `test_worldmap_proc_iter_null_guard.py::ProcFindNextSourceGuardTests.test_named_helpers_contain_the_guard` | Stable audit ID now compiles break/continue exhausted-iterator fixtures and traverses their null successor. | The `break` fixture reaches a compiled function exit. | The `continue` mutation reaches another iterator call before an exit. |
| `test_worldmap_proc_iter_null_guard.py::ProcFindNextCodegenTests.test_release_build_can_still_leave_the_iterator_loop` | Every optimized world-map `Proc_FindNext` relocation has a null path that reaches an actual function exit before another iterator call. | All generated release iterator calls preserve exhausted-list behavior. | The compiled `continue` mutation proves a branch shape alone is insufficient. |

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
