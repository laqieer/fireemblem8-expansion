# Confined dependency-only compiler commands

Issue [#228](https://github.com/laqieer/fireemblem8-expansion/issues/228)
adds a **framework capability** above the
[live producer contract](ownership-probe-producers.md): a real HOST C
dependency-preprocessor action, not arbitrary native compilation.

## Public command profile

```python
query = Command(
    (
        "/usr/bin/cc", "-E", "-I", "include/first", "-Iinclude/second",
        "-iquote", "include/quotes", "-nostdinc", "-undef",
        "-DENABLED=1", "-UOLD_SETTING", "src/query.c",
        "-MM", "-MG", "-MT", "query.o",
    ),
    code=("include/first/header.h", "include/second/header.h"),
    sources=("src/query.c",),
    outputs=("out/query.d",),
    dependency_only=True,
)
result = probe.command(query)
dependency_file, = result.generated
```

Use the caller's existing `ProbeSession` and its one source authority/budget.
The boolean defaults to `False`; normal commands, native ELF compilation and
native handles retain their existing behavior. A plain `Command` still cannot
execute `/usr/bin/cc`. The dependency flag must be an actual boolean and
cannot combine with a `NativeTool`.

The profile admits one repository-relative `.c` source, present in `sources`,
one repository-relative `.d` output and one canonical `-MT` target.
`-E`, `-MM`, `-nostdinc` and `-undef` are required; `-MG` is optional.
Repeated mode flags and unsupported options reject before payload launch.
`-I`, `-iquote`, `-D`, `-U` and `-MT` accept joined or separate values.
The original option order is retained, including repeated include paths and
interleaved definitions/undefinitions. Include paths are canonical relative
names or `.`; definitions use symbolic object-like macro names, and `-U`
accepts a name without a value. Target names use the existing bounded Make
target syntax, not arbitrary Make expressions.

Raw `-I` and `-iquote` operands beginning with `=` or literal `$SYSROOT`
reject in both joined and separate forms, including bare/no-slash forms.
GCC treats those prefixes specially on a configured sysroot. Characters
inside a canonical name, such as `local/=headers` or
`local/$SYSROOTheaders`, remain literal and supported. Noncanonical `./` or
`..` components still reject; they are not stripped into special syntax.
This is profile-ambiguity rejection, not a claim that the installed empty
default sysroot supplied another escape. Explicit `--sysroot` remains
unsupported.

There is no `-c`, `-S`, alternative dependency mode, output override,
response file, plugin, specs, wrapper, forced include, arbitrary language,
assembler or linker authority. The trusted adaptation adds only `-MF` for
the declared private `/work` destination. Necessary private parent directories
spend the existing creation allowance. It does not run a shell or synthesize
dependency content.

## Actual execution and receipts

The existing compiler resolver selects the system C driver and its actual
`cc1` once per session. System paths and aliases must resolve within the
trusted ordinary compiler roots. Only those two resolved images can execute,
in driver/frontend order, through the existing channel-free compiler capsule.
`ProcessOutput.executed` records their **successful kernel exec transitions**;
an incomplete or contradictory receipt rejects. It is not a `cc -###` plan.

Compiler runtime access retains the existing recursively read-only `/usr`
mount, but D decides source/runtime authority before the generic compiler
and Python/library prefix allowances. The trusted interpreter already
identified for the supported host resolves the driver/cc1 library paths with
bounded `--inhibit-cache --list` queries; the trusted driver supplies its
finite search directories. D admits only the exact resolved program,
interpreter and shared-library files, directory metadata, and the bounded
genuinely absent driver/loader probes needed by that profile. File types
and statuses remain real, and directory metadata grants no enumeration or
member contents. Unsupported runtime resolution rejects instead of falling
back to the broad compiler policy.

Other host preprocessing reads and searches reject explicitly, including
missing names, nonregular objects, stock aliases and parent spellings.
This covers system/local includes, GCC private/include-fixed/libexec paths,
library and sysroot trees, Python data and extensionless files—not a `.h`
filter or two-prefix blacklist. Generic native compilation remains unchanged.
There is no compiler binary snapshot, new privilege service, optional R
dependency, resource increase or guessed dependency parser.

If a caller also requests delivered R inputs, their captured identity remains
part of Make's execution digest. Those inputs do not become dependency source
declarations or compiler execution grants. The default empty runtime-input
profile and the independently configured runtime profile share P's live
framing, publication and resource lifetime.

`generated[0].data` contains the actual dependency bytes; `stdout` is empty
because the compiler wrote its dependency output to the declared file.
Diagnostics remain raw `stderr` bytes. `artifact` is `None`, and no native
tool handle is issued. Keep these bytes intact until an explicitly named
textual consumer decodes them. In particular, let GNU Make parse dependency
syntax, escaped filenames and continuations rather than copying a `.d` parser.

`sources` is the exact required consumed set. An optional explicit `code`
pool admits additional candidate headers; unused pool members are not
dependency provenance. The union of `consumed` and `code_consumed` selects
`input_identities` from identities captured **before that execution**, retaining
actual bytes/modes for generated inputs too. Later host edits or later
publication cannot relabel earlier compiler output. A caller can instead
declare the entire known source/header union in `sources`.

## Truthful search and generated headers

Dependency-only search admits read/metadata probes of genuinely absent names
within the complete `/repo` backing, plus metadata of actual include-search
directories. Existing undeclared files reject; they are never hidden to make
`-MG` or `__has_include` choose a false absence. Forbidden symlink/gitlink
namespaces and non-directory ancestors retain the lower type policy.
An include directory is not permission to read its members or enumerate it.
The existing metadata recorder retains real kernel status, operation, masks
and complete returned buffers; no metadata field is fabricated or masked.

With `-MG`, a legitimately missing header may appear in the real `.d` without
becoming a consumed existing source or receiving an invented input identity.
Without `-MG`, the real compiler error remains terminal. Previously published
headers can be explicitly admitted through P's active `published_sources`.
The two-phase boundary remains read-only `/repo`, private `/work`, then
validated publication; the compiler does not read its own `.d` through `/repo`.

## One outer Make

Register the original dependency recipe string against the typed command:

```python
observed = probe.make(
    "query.o",
    variables=("MAKEFILE_LIST", "MAKE_RESTARTS"),
    commands={
        "mkdir -p out && cc -E -nostdinc -undef src/query.c -MM -MG -MT query.o > out/query.d":
            Command(
                ("/usr/bin/cc", "-E", "-nostdinc", "-undef", "src/query.c",
                 "-MM", "-MG", "-MT", "query.o"),
                sources=("src/query.c",), outputs=("out/query.d",),
                dependency_only=True,
            ),
    },
)
```

The corresponding source Makefile uses an ordinary include/remake:

```make
include out/query.d
out/query.d: src/query.c
	@mkdir -p out && cc -E -nostdinc -undef src/query.c -MM -MG -MT query.o > out/query.d
query.o: ;
```

P publishes the validated `.d` only after the actual compiler exits
successfully. Make then reads it and performs its own restart. Generated
headers may be published first in that same live Make invocation; this is not
a nested `session.make()` with generated outputs. Nested publication lifetime,
source admission and vfork parking remain P-owned mechanisms, not additional
requirements or privately duplicated fixes in D's standalone contract.

Every output-producing dispatch executes genuinely, including repeated
identical registrations. Compatible pure observations still use the existing
cache/complete-metadata contract; no blanket cache disable or stale output
reuse is added. Compiler resolution, preparation, actual execution, metadata,
result capture, cache, publication and cleanup spend the same report lifetime.
Parked Make/helper processes and funded VM remain reserved throughout cc/cc1
work. No count, byte limit, deadline or resource category increases.

## Evidence, compatibility and delivery boundary

Run the focused source-only case:

```sh
python3 -m unittest scripts.validation_ownership.tests.test_dependency -v
```

The existing native CI owner includes this suite only in full Build mode.
Metadata-only and review-first preflight runs do not provide native test or
full Build evidence.

The indexed human procedure is
[`TC-WORKFLOW-PROBE-DEPENDENCY-001`](test-cases/workflow-governance.md#tc-workflow-probe-dependency-001-observe-real-confined-compiler-dependencies).
It compares ordinary/confined bytes, source/header unions, changed search and
macro order, real generated headers, escaped dependency names and native Make
restarts. A generated header is replaced between identical compiler calls;
both dependency receipts and a cached pure `.d` reader must remain current.
The real `worldmap_tm_confront.c` comparison uses this checkout's
source/header bytes and observed host argv. Absent agbcc/generated include
inputs remain genuine missing headers under `-MG`; no toolchain installation
or generated game content is substituted for the fixture.

The pre-feature public rejection and original producer evidence remain
recorded in [#206's compiler checkpoint](https://github.com/laqieer/fireemblem8-expansion/issues/206#issuecomment-5563209086).
The unapproved integrated d9 implementation is historical evidence, not an
approval or restoration source.

D depends on P/#225 and transitively the delivered core/#206; its immediate
branch base is `delivery/d581-issue-225`, stack depth one. V/#226 and R/#227 are
independent and not prerequisites. Shared compiler/source/result/test/docs
contracts can conflict and require normal parent refresh. Complete P delivery
still gates bottom-up merging; full #180/PR #186 CURRENT/BASE/domain integration
and nested generated publication are separate. No other feature conflict,
manual criterion, gameplay, ROM/RAM, save/config, locale, generated game data,
modern/archival profile, package, CI topology or publisher change is involved.
On regression, revert D or fix forward without loosening the lower boundary.
