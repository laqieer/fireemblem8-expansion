# Authenticated issue-scoped Git publication

Issue [#205](https://github.com/laqieer/fireemblem8-expansion/issues/205) is a
**framework capability**: a protected publication boundary for local delivery
coordinators and CI coordinators. It is the independent foundation for
[#178 / PR #191](https://github.com/laqieer/fireemblem8-expansion/pull/191), not
a replacement for that handoff validator.

The production consumer is
[`BrokerClient`](../scripts/workflow_pilot/git_broker.py), also exposed through
the source-only installed `publish` and `readback` commands. It talks to the production
`serve` command, which uses
[`PublicationStore`](../scripts/workflow_pilot/git_broker_store.py). Tests
exercise real Git receive-pack, real TLS, the actual consumer and the same
server exchange. They do not authorize a same-UID deployment.

**No external service is implicitly installed or assumed available.** An
ordinary development checkout is not an authority installation. A missing
service, same-UID arrangement, user-owned installation, wrong peer, wrong
certificate, missing plan or uncertain result fails closed. Bare `make`,
modern debug/release, archival builds, saves, generated data, localization and
ROM/RAM budgets are unchanged. No feature flag or Build-topology change is
needed.

## Authority and non-goals

The supported production boundary is **three distinct Linux principals**:

1. A dedicated, unprivileged broker owns its private state, Git credentials
   and the optional local bare remote.
2. A dedicated unprivileged trusted coordinator owns its client TLS private key and runs only the
   root-installed client and trusted handoff validator. Its UID is different
   from the broker and every implementation/candidate UID.
3. Unprivileged candidates have neither identity. They may author input bytes; they never
   execute under the coordinator/broker or inherit their descriptors.

UID 0 is not an accepted broker, coordinator or candidate identity. The
external installation/test administrator is outside these protocol roles.
The coordinator cannot read the broker's credential or local-remote state.

The source, installation JSON, trust roots and all their parent directories
are root-owned and not group/other writable. Broker state and secrets are
mode `0700` directories / `0600` single-link regular files, with no symlink
traversal. The coordinator's private key has the corresponding coordinator
ownership. Root/host administrators remain trusted. Do not install authority
code by linking or copying a writable candidate tree and then executing it.

Each connection requires both:

- Linux `SO_PEERCRED` matching the installed other UID; and
- TLS 1.3 mutual authentication, exact client certificate pinning and exact
  server certificate pinning, plus hostname `workflow-pilot-git-broker`.

The server signs its fresh session and result with an independently pinned
RSA response key. Neither an abstract Unix address nor filesystem permissions
under one UID establish authority. The filesystem socket's parent cannot be
replaced by a candidate. A candidate that can resolve the socket path is refused
at `connect()` by kernel permissions, before it can occupy the listen backlog.
The broker-owned socket is mode `0660` with the protected installation's
`socket_gid`: a dedicated group containing only the broker and trusted
coordinator, never a candidate's primary or supplementary group. The server
sets and verifies owner/group/mode before listening, and the client verifies
them before connecting. Inherited access ACLs reject rather than silently
granting another user access despite mode `0660`. Peer-UID and mTLS checks
remain mandatory; a group member without the expected UID or an approved
client certificate still rejects.

There is **no generic credential, signing, URL, ref, shell-command, helper or
push API**. Every installation names one repository identity and one issue.
The only writable refs are:

```text
refs/heads/workflow-pilot/authority/issue-N
refs/heads/workflow-pilot/authority-anchor/issue-N
```

No `master`, tag, deletion, force-rewrite, arbitrary remote or arbitrary
candidate command is authorized.

## Exact signed plan and PR191 integration

[`git_broker.schema.json`](../scripts/workflow_pilot/git_broker.schema.json)
is the closed public wire schema. Canonical JSON is sorted-key ASCII, compact
separators and one trailing LF. Duplicate keys, floats, non-finite values and
noncanonical bytes reject. Signatures are canonical Base64 RSA
PKCS#1-v1.5/SHA-256, with a 2048–4096-bit modulus and exponent 65537. Public-key
identity, canonical padding and RSA representative bounds are enforced.
Repository and issue-ref patterns require strict end of input, not an end-of-line
match: a final LF or any other trailing input rejects. Repository components
`.` and `..` are reserved and reject in both the schema and installed-policy
parser; names such as `.git` and `...` retain their existing lexical meaning.

PR191 should retain its complete handoff/history/receipt/PR/ruleset validator
and trusted installation. After that validator approves a publication:

1. Preserve the exact `plan_history_authority()` `record`, including its
   signed publication attestation, and complete `anchor_record_template` with
   the newly created authority commit. Preserve `authority.json` (schema 2),
   `anchor.json` (schema 1), issue refs and exact single-parent ancestry.
2. In the trusted coordinator, export a **full, non-thin pack**, including
   precisely the closure reachable from both new commits. Do not execute
   candidate Git config, worktree scripts, helpers or checkouts to export it.
   Parent history is included; unused objects, tags and missing bases reject.
3. Call `git_broker_protocol.unsigned_plan(policy, ...)`. Supply `operation`,
   `sequence`, the attestation's nonce, canonical issuance/expiry, old/new
   authority and anchor OIDs, pack bytes and exact object IDs. This constructor
   accepts no arbitrary ref or endpoint.
4. Have the **existing external terminal signer**, not the candidate, approve
   and sign `signed_records.signed_payload(PLAN_DOMAIN, plan)`. Add canonical
   Base64 as `signature`. The signer must revalidate PR191's complete approved
   result before signing this exact terminal plan; do not create a generic
   sign-any-bytes endpoint. `Policy.signer` uses PR191's full six-field signer
   record and existing `workflow-pilot-agent-coordinator-attestation-v2`
   public identity, not a key supplied by the request.
5. Invoke the installed CLI, which source-loads
   `BrokerClient(client_installation).request(plan, pack)`. An embedded
   coordinator must use an equivalently protected source-only bootstrap,
   not ordinary `sys.path` imports of the installed package or `python -m`.
   Accept only an authenticated `published` response
   whose refs equal the two exact new OIDs. Keep PR191's terminal receipt,
   protected history, live authority confirmation and eligibility checks.
6. After loss of a reply, use a **new** authenticated `readback` session with
   the original signed plan. Never retransmit a publication or assign a new
   nonce to the same unverified operation.

The broker independently checks the signature, actor/key/client/deployment,
repository name and numeric ID, issue, canonical installed endpoint, operation,
sequence, old/new OIDs, nonce, issuance/expiry, pack digest/count/closure and
object bounds. It parses both new records and their predecessors, checks the
exact parent edges, issue, sequence, anchor linkage, frozen signer/ruleset/
bypass/delivery policy and bootstrap/advance/one-time-bind transitions. It
verifies the embedded publication signature and complete plan binding,
receipt/carrier digests and PR-observation signature/digest. PR191's complete
semantic handoff verification remains the terminal signer's responsibility;
this foundation must not replace that verification with these narrower
publication checks.

The publication observation must be at most two seconds before full-plan
issuance. The full plan lasts at most 30 seconds; broker execution and readback
must complete within the live plan and session deadlines. Request and response
contain the fresh server session nonce. Results bind the complete signed plan
digest, exact live refs, completion time and fresh session deadline.

### One use, crashes and atomicity

Before receiving a pack or accessing credentials, an SQLite `FULL`
synchronous transaction consumes the nonce. A single lifetime file lock
prevents two service owners. The journal is bound to the complete installed
policy and retains the last published sequence and exact authority/anchor
pair, so an empty/rewound remote, copied journal, fresh-nonce ABA or skipped
sequence cannot reset publication authority.

A fresh journal permits only genesis into two absent refs. **Keep and back up
the protected journal with the deployment.** There is intentionally no
automatic adoption of existing refs or journal reset.

Publication uses one `git push --atomic` with two exact force-with-lease
expectations, after independently proving both commits are direct
fast-forwards. It then reads both exact remote refs. Protected server hooks
are not disabled by client `core.hooksPath` overrides; no such override is
passed to local receive-pack.

Incomplete input spends its reservation. An interrupted executing/reserved
operation becomes `uncertain` on restart and blocks further publications.
Readback is non-mutating and cannot reuse a capability. A timeout can occur
after a remote accepted an atomic transaction: **do not claim rollback or
success from a lost reply**. An uncertain operation requires protected
reconciliation of the exact remote transaction and deadline evidence; this
version does not expose a journal-clearing/recovery mutation API. Missing
completion-time evidence is not reconstructed from current refs. The
dependent handoff remains ineligible, rather than guessing success.

## Deployment and fail-closed preflight

Dependencies: Linux Unix sockets/`SO_PEERCRED`, sealed `memfd`/procfs handles,
Python 3.10+, Git with atomic push support, OpenSSL 3 and systemd cgroup
supervision. SSH additionally uses the existing OpenSSH `ssh` and `ssh-keygen`
executables. Network deployments require the root-owned system CA bundle at
`/etc/ssl/certs/ca-certificates.crt`. The focused tests use OpenSSH for
synthetic keys/configuration and the existing `jsonschema` validator. No new
tool is installed by broker startup or tests. The production modules use the
Python standard library only.

An external administrator must provision the dedicated principals and
root-owned code installation. **Do not create users, alter permissions on
shared trees, change host settings or start a credentialed service merely to
make a local test pass.** Use a disposable authorized VM/container or an
already provisioned protected service. A one-UID user namespace or an
unauthenticated local test daemon is not a substitute.

Install these reviewed source files, without writable package directories,
under `/opt/fe8-git-broker/scripts/workflow_pilot/`:

- `__init__.py`, `signed_records.py`, `git_broker_protocol.py`,
  `git_broker_store.py`, `git_broker.py`.

All parents and files must be root-owned; code files are `0644`, public
directories `0755`. Execute the trusted `git_broker.py` file with isolated
Python (`-I`). Before importing **any** repository module, that bootstrap
checks every source and parent in the existing installed-module closure.
It opens each source once without following a link, verifies the opened
regular file's owner/mode/link count/size, and captures its bytes. Only after
the entire closure passes does it compile those bytes into fresh modules,
including the package initializer and broker itself. Package search paths
are empty and imports outside that closure reject; existing module-cache
entries cannot substitute code. There is no pathname or bytecode fallback.

Remove stale caches as installation hygiene, but correctness does not depend
on their absence: adjacent candidate-writable unchecked-hash `.pyc` files are
never loaded. `-B` alone only disables bytecode **writes**, not those reads.
Even `--help` from an unprotected installation fails before local imports.
The root-controlled bootstrap and system Python/standard library remain
trusted deployment prerequisites; executing an attacker-modified launcher
cannot establish authority.

Install the reviewed
[`workflow-pilot-git-broker@.service`](../scripts/workflow_pilot/deployment/workflow-pilot-git-broker@.service)
template using the already provisioned `fe8-git-broker` account. Provision the
dedicated `fe8-git-coordinator` socket group externally; the unit gives it to
the broker through `SupplementaryGroups` while keeping the broker's private
primary group for state. Give the coordinator that socket group too. Its
numeric GID must equal `socket_gid` in both protected manifests. Preflight
requires the executing principal to hold that group. The external
administrator must ensure no other primary/supplementary memberships grant
candidate access; neither startup nor tests create host users or groups.
The instance
name identifies an issue installation, for example `issue-205`; it does not
authorize an issue number supplied by a request.

The unit creates only its own runtime/state directories, has no capabilities,
limits tasks/memory/files, and kills its complete cgroup on stop/failure.
Commands have an additional independent eight-second hard-kill timeout,
shortened to the remaining plan/session lifetime, even if the broker itself
is SIGKILLed.
Do not use unmanaged background Git helpers in production.
Normal `serve` SIGTERM unwinds active requests, subprocesses, the socket and
journal before exiting 0. Cleanup errors and SIGINT still fail; non-serve
interruptions are not converted to success. No `SuccessExitStatus=2` exception
is needed. A clean service stop does not certify a pending publication:
incomplete packs remain spent, and interrupted receive-pack retains journal
uncertainty and the existing protected reconciliation hold.

Both installation files are canonical JSON, not shell configuration. Their
closed field sets are `SERVER_FIELDS` / `CLIENT_FIELDS` in `git_broker.py`.
The fields are:

| Shared field | Provisioned value |
| --- | --- |
| `schema_version`, `role` | `1`, and `server` or `client` |
| `policy` | Exact `Policy` fields below; no request-derived authority |
| `broker_uid`, `coordinator_uid`, `candidate_uids` | Actual distinct kernel UIDs; candidate list nonempty |
| `socket` | `/run/workflow-pilot-broker-issue-205/broker.sock` or the corresponding protected instance path |
| `socket_gid` | Nonzero dedicated broker/coordinator-only group GID; provision membership outside the service |
| `certificate`, `private_key`, `ca_certificate` | Absolute role-specific certificate/key/CA paths |
| `server_certificate_sha256` | SHA-256 of the externally provisioned server certificate's DER bytes |
| `response_public_key` | `algorithm`, lowercase `modulus_hex`, `exponent`; pinned out of band |

Server-only fields are `state` (the instance's private `/var/lib` directory),
`response_private_key` (matching the pinned response key), and `transport`.
Existing manifests without `socket_gid` fail closed and must be reprovisioned
by the external owner; the signed-plan wire schema and journal identity are
unchanged.
The private TLS/response/transport keys are readable only by the broker;
the client's TLS key only by the coordinator. The CA **private** key is not
installed in either service. Certificates must be current and issued by the
configured CA; the server certificate includes SAN
`DNS:workflow-pilot-git-broker`. Renew pins out of band, not from server output.

`policy` has exactly `deployment_id` (fresh 64-hex installation identity),
`repository`, `repository_id`, `issue`, `endpoint`, `actor_id`,
`client_certificate_sha256`, `signer`, `ruleset_id` and
`authorized_bypass_actors`. The signer and normalized bypass records come from
the protected PR191 installation. A User bypass includes matching `actor_id`
and `database_id`; non-user types are explicit, not guessed from numeric IDs.
The policy and journal are frozen together.

Supported transports (closed root-installed **transport policy**, not request
fields):

- `{"kind":"local"}`: endpoint `file:///absolute/protected/remote.git`, a
  **strict descendant** of the configured broker state directory. The state
  directory itself, ancestors, siblings (including similarly prefixed names)
  and protected remotes elsewhere such as `/srv/...` fail preflight. This
  matches the shipped unit's `ProtectSystem=strict` write boundary; ownership
  alone does not make an outside remote writable by that unit. Symlink and
  traversal paths remain prohibited. Native bare Git `receive.denyNonFastForwards`,
  `receive.denyDeletes` and `core.bare` must be true. Only the closed standard
  bare/receive config is allowed; includes, alternate object stores, external
  hook paths, special files and links reject. The complete config, hooks,
  refs and objects must be inaccessible to candidates.
- `{"kind":"https","credential_kind":"github-fine-grained-user-pat","token_file":"...","helper":"..."}`:
  endpoint is exactly `https://github.com/OWNER/REPOSITORY.git`. `helper` is
  this exact installed `git_broker.py`. Only a GitHub fine-grained personal
  access token with the `github_pat_` prefix is supported. A prefix or
  protected file **does not prove identity**: the exact token must
  authenticate `GET https://api.github.com/user`, returning `type: "User"`
  and the integer `id` equal to `policy.actor_id`, which must also be the
  installed always-bypass User/database ID. Git receives that same verified
  token through the exact-host/repository helper, never through argv or an
  environment value. Redirects and ambient credentials/configuration are
  disabled. The token needs the disposable repository's required Git write
  permission; identity verification does not grant repository permissions.
- `{"kind":"ssh","credential_kind":"github-user-ed25519","key":"...","known_hosts":"...","public_key_fingerprint":"SHA256:..."}`:
  endpoint is exactly `ssh://git@github.com/OWNER/REPOSITORY.git`. The key
  must be an unencrypted plain Ed25519 **User** authentication key. Its public
  SHA-256 fingerprint (OpenSSH's canonical unpadded `SHA256:` Base64 form)
  is pinned out of band in this protected transport policy. The broker
  derives the actual public key with `ssh-keygen -y`, checks the fingerprint,
  and authenticates `ssh -T git@github.com` with the very same private key
  and root-pinned `known_hosts`. Only exit 1 and GitHub's exact documented
  `Hi USERNAME! You've successfully authenticated, but GitHub does not provide shell access.`
  greeting are accepted. A TLS-verified `GET
  https://api.github.com/users/USERNAME` must then return that exact login,
  `type: "User"` and the integer `id` equal to the installed actor/bypass ID.
  The public API lookup alone is **not** key proof: it follows the
  authenticated, host-verified SSH greeting and pinned key derivation.
  No agent, SSH certificate, alternate identity/configuration, global
  known-host file, DNS host-key trust, host-key update, proxy, forwarding,
  prompt or password fallback is allowed.

Network identity verification runs at server preflight and **before every
remote Git invocation**, including readback. The production broker captures
the protected token/private-key bytes once, checks the opened file's owner,
mode, link count and size, and copies them into a private Linux memfd sealed
against writes, growth, shrinkage and seal changes. SSH host-key trust is
captured into another sealed memfd. The broker keeps those exact handles open
through identity verification and the subsequent Git command; helper/SSH
children access the broker's procfs handles rather than reopening the original
files. Replacing an original file after verification cannot substitute the
credential or host trust used by Git. Handles close on every exit, and no
credential snapshot is written to a filesystem or sent to the coordinator.

The installed `credential-check` subprocess is an internal closed worker,
not a new daemon or caller-facing credential API. It can query only
`api.github.com:443`, uses the fixed protected system CA bundle and hostname
verification, and ignores proxy/CA environment overrides. It never follows
redirects or retries, and rejects malformed, duplicate-field, oversized,
non-200 or compressed identity responses. The existing independent
eight-second subprocess watchdog bounds the whole identity check, including
DNS, TLS, the SSH probe and body reading; remaining session/plan lifetime
shortens it. Its only successful output is `verified`, not credential
material, remote messages or user profile data.

**Explicit limits:** classic PATs, OAuth tokens, App user/installation tokens,
deploy keys, SSH certificates, encrypted keys, other key algorithms and GitHub
Enterprise are unsupported and fail closed. Non-User bypass entries do not
authorize any of those credential types. Old network manifests lacking the
explicit credential kind/key fingerprint must be reprovisioned; local
transport and the signed plan schema are unchanged. A changed GitHub greeting,
unavailable/rate-limited API, stale key registration, missing host trust or
ambiguous principal rejects, not a success-shaped fallback. These are fresh
observations of GitHub's current identity binding, not a promise that a remote
administrator can never revoke/reassign a key. Keep key ownership and account
registration under the external deployment's control.

Identity contracts use GitHub's
[authenticated-user endpoint](https://docs.github.com/en/rest/users/users#get-the-authenticated-user)
and [documented SSH test](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/testing-your-ssh-connection).
Do not derive a trust root, bypass principal or key pin from a submitted plan,
an unauthenticated banner, or an API response.

Preflight and client usage, run under the **already provisioned appropriate
principal**, not from candidate code:

```bash
/usr/bin/python3 -I /opt/fe8-git-broker/scripts/workflow_pilot/git_broker.py \
  preflight-server --installation /etc/fe8-git-broker/issue-205.json

/usr/bin/python3 -I /opt/fe8-git-broker/scripts/workflow_pilot/git_broker.py \
  preflight-client --installation /etc/fe8-coordinator/issue-205.json

/usr/bin/python3 -I /opt/fe8-git-broker/scripts/workflow_pilot/git_broker.py \
  publish --installation /etc/fe8-coordinator/issue-205.json \
  --plan ./signed-publication.json --pack ./exact-publication.pack

/usr/bin/python3 -I /opt/fe8-git-broker/scripts/workflow_pilot/git_broker.py \
  readback --installation /etc/fe8-coordinator/issue-205.json \
  --plan ./signed-publication.json
```

`preflight-client` authenticates a live service but cannot publish. `publish`
returns exit 0 only for an authenticated `published` result; exit 1 denotes a
typed non-success result and exit 2 a closed preflight/protocol failure.
`readback` is informational, including after plan expiry; it does not grant
fresh handoff eligibility. After stopping the exact unit, let its runtime
directory clean up. Do not unlink an existing live socket, delete the
persistent journal or send UID/name-wide signals.

### Bounds and confidentiality

| Resource | Hard bound |
| --- | --- |
| Each source in the fixed installed-module closure | 1 MiB, captured once before local imports |
| Canonical request JSON | 256 KiB |
| Signed hello/result | 8 KiB |
| Full pack | 16 MiB; 4,096 objects |
| Expanded objects | 2 MiB each; 64 MiB total |
| Plan/session lifetime | 30 seconds |
| Git/OpenSSL subprocess | 8 seconds; independent timeout cleanup |
| Complete network credential identity worker | 8 seconds; shortened to remaining plan/session time |
| Token / SSH private key / host-key snapshot | 1 KiB / 16 KiB / 64 KiB |
| GitHub identity body / SSH identity output | 16 KiB / 4 KiB |
| Subprocess output | 4 MiB, never replayed into responses/logs |
| Active request / queued connections | One / four |
| Nonce journal | 100,000 consumed entries; no replay-enabling eviction |

Packs are indexed with strict fsck in a new private bare object database;
decompression has CPU/address-space/file limits before object sizes can be
trusted. A bounded `git cat-file --batch-check` obtains the reconstructed size
of **every verified object**, including delta objects, before closure checks
or remote access. Both the individual and total budgets count those resolved
sizes: `git verify-pack -v` reports delta instruction sizes for deltified
entries, which can be much smaller and cannot enforce these budgets.
No candidate config, attributes, replacements, alternates, hooks, template or
worktree executes. Pack SHA-256 and object IDs identify the signed external
input/closure; they are not a committed source or ROM identity ledger.

All child environments are rebuilt from an allowlist. Raw Git/SSH/HTTPS/helper
output is discarded, not interpolated into errors. Failed/oversized requests
cannot ask for secret files or arbitrary commands. Temporary operation
directories are private children of the broker state, not system temporary
directories.

## Canonical timestamp contract

The reporter and broker use the same `signed_records.parse_utc()` function.
The schema's exact Gregorian grammar accepts years 0001–9999, correct leap
days, hours **00–23**, minutes/seconds 00–59, optional one to six fractional
digits, uppercase `T`, and a final uppercase `Z`. It rejects `24:00:00`,
offsets (including `+00:00`), leap seconds, compact/week/ordinal dates,
Unicode digits, extra precision and trailing whitespace.

No `datetime.fromisoformat()` parsing or rounding controls signed lifetimes.
PR191 must reuse this function instead of restoring its runtime-dependent
parser or a separate timestamp grammar.

## Acceptance evidence and external hold

The full tester procedure is
[`TC-WORKFLOW-AUTHENTICATED-GIT-BROKER-001`](test-cases/workflow-governance.md#tc-workflow-authenticated-git-broker-001-publish-only-through-authenticated-external-authority).
There is no visual/audio/UX or human-review criterion.

Noncredentialed real Git/TLS unit tests are deterministic protocol evidence.
Loader regressions substitute only source ownership when exercising the real
captured-byte loader; they also run an unchecked-hash cache under `-B` as a
negative control and verify rejection before imports. Socket regressions
inspect real Unix socket metadata/ACLs and parse the deployment unit. Service
regressions run real processes/TLS/Git with explicitly synthetic installation
and peer authority, then signal idle, reserved and executing requests. They
check clean SIGTERM, failing SIGINT/cleanup, child termination, spent nonces
and preserved uncertainty. None is a same-UID protected positive.
The credential regressions use synthetic GitHub responses with the actual
identity validators, real Ed25519 derivation, parsed OpenSSH configuration,
sealed-handle/file-substitution controls and a real TLS rejection of an
untrusted fixture CA. They prove wrong-user/wrong-key rejection before remote
Git, not live GitHub authentication.
They are **not evidence of protected three-principal installation or GitHub
authentication**. The required protected fixture fails (does not skip or
pretend success) when the OS boundary is unavailable. GitHub HTTPS/SSH
acceptance additionally needs a credentialed disposable repository with
actual authority/anchor protections, a provisioned broker, current trusted
signer and fresh exact plans. Do not fabricate that result from local mocks
or uncredentialed ls-remote.

Every protected plan adversary receives a fresh signed plan/nonce and its
matching full pack. Only explicit replay cases reuse a consumed plan.
The rejection hook is removed after its dedicated check, and a fresh
publication must then succeed before the field-validation cases run.
The root-controlled fixture invokes the production `serve` entry point with
test-only observers around the real plan validator and reservation method.
Both service and client use the captured-source bootstrap. The fixture
allows its actual candidate UID to rewrite an adjacent unchecked-hash cache,
proves the ordinary `-B` importer executes that negative control, and requires
the production entry point and subsequent service/client operations never to
execute it. It gives the socket group only to broker/coordinator children
and requires a candidate's actual `connect()` to fail with permission denial.
A post-connect protocol rejection, timeout or unavailable listener does not
satisfy direct-connect denial. Normal SIGTERM must return 0 and remove the
socket, including before a clean restart.
These observers record only plan digests and returned/rejected stages in
broker-private state; they neither replace checks nor change the wire protocol.
The controller requires the expected validation stage and unchanged complete
journal/ref snapshots. Timeouts, connection loss without the matching broker
observation, signed hook rejection and downstream failures are not validation
evidence. Replays must pass plan validation and reject reservation, with an
already consumed exact plan in the unchanged journal.

The existing Build `host-tests` owner discovers broker tests through
`isolated_launcher.py reporter-tests`, including the same fixture helpers
over real noncredentialed Git/TLS. Mutation controls omit each of the six
tested field checks individually; the rejection oracle must fail even when a
later journal, object, hook or deadline check would reject publication.
The protected three-UID command is not invoked by unittest discovery.
No privileged fixture/credentialed workflow or Build topology change is
introduced; all protected-installation, live GitHub credential and deployed
cgroup acceptance holds remain.

Dependencies: existing signed-record, exact-repository, protected User bypass
and trusted external installation contracts, plus the narrow GitHub credential
contracts above. Dependent: PR191/#178. Conflict: its provisional
unauthenticated broker/duplicate parser must be replaced; all other #178
safeguards remain. No gameplay/profile/save/resource interactions.

Rollback is a dedicated revert; dependent publication remains blocked rather
than falling back to an unauthenticated or generic Git transport.
