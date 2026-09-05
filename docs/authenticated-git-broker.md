# Authenticated issue-scoped Git publication

Issue [#205](https://github.com/laqieer/fireemblem8-expansion/issues/205) is a
**framework capability**: a protected publication boundary for local delivery
coordinators and CI coordinators. It is the independent foundation for
[#178 / PR #191](https://github.com/laqieer/fireemblem8-expansion/pull/191), not
a replacement for that handoff validator.

The production consumer is
[`BrokerClient`](../scripts/workflow_pilot/git_broker.py), also exposed through
the installed `publish` and `readback` commands. It talks to the production
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
replaced by a candidate. A candidate that can reach the socket is disconnected
before TLS or request handling; a CA-valid but unapproved client certificate
also rejects.

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
5. Invoke the installed `BrokerClient(client_installation).request(plan,
   pack)` or installed CLI. Accept only an authenticated `published` response
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

Dependencies: Linux Unix sockets/`SO_PEERCRED`, Python 3.10+, Git with atomic
push support, OpenSSL 3 and systemd cgroup supervision. The focused schema
tests also use the existing `jsonschema` validator. No new tool is installed
by broker startup or tests. The production modules use the Python standard
library only.

An external administrator must provision the dedicated principals and
root-owned code installation. **Do not create users, alter permissions on
shared trees, change host settings or start a credentialed service merely to
make a local test pass.** Use a disposable authorized VM/container or an
already provisioned protected service. A one-UID user namespace or an
unauthenticated local test daemon is not a substitute.

Install these reviewed source files, without candidate-writable bytecode or
package directories, under `/opt/fe8-git-broker/scripts/workflow_pilot/`:

- `__init__.py`, `signed_records.py`, `git_broker_protocol.py`,
  `git_broker_store.py`, `git_broker.py`.

All parents and files must be root-owned; code files are `0644`, public
directories `0755`. Install the reviewed
[`workflow-pilot-git-broker@.service`](../scripts/workflow_pilot/deployment/workflow-pilot-git-broker@.service)
template using the already provisioned `fe8-git-broker` account. The instance
name identifies an issue installation, for example `issue-205`; it does not
authorize an issue number supplied by a request.

The unit creates only its own runtime/state directories, has no capabilities,
limits tasks/memory/files, and kills its complete cgroup on stop/failure.
Commands have an additional independent eight-second hard-kill timeout,
shortened to the remaining plan/session lifetime, even if the broker itself
is SIGKILLed.
Do not use unmanaged background Git helpers in production.

Both installation files are canonical JSON, not shell configuration. Their
closed field sets are `SERVER_FIELDS` / `CLIENT_FIELDS` in `git_broker.py`.
The fields are:

| Shared field | Provisioned value |
| --- | --- |
| `schema_version`, `role` | `1`, and `server` or `client` |
| `policy` | Exact `Policy` fields below; no request-derived authority |
| `broker_uid`, `coordinator_uid`, `candidate_uids` | Actual distinct kernel UIDs; candidate list nonempty |
| `socket` | `/run/workflow-pilot-broker-issue-205/broker.sock` or the corresponding protected instance path |
| `certificate`, `private_key`, `ca_certificate` | Absolute role-specific certificate/key/CA paths |
| `server_certificate_sha256` | SHA-256 of the externally provisioned server certificate's DER bytes |
| `response_public_key` | `algorithm`, lowercase `modulus_hex`, `exponent`; pinned out of band |

Server-only fields are `state` (the instance's private `/var/lib` directory),
`response_private_key` (matching the pinned response key), and `transport`.
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

Supported transports:

- `{"kind":"local"}`: endpoint `file:///absolute/protected/remote.git`, below
  the broker's private state. Native bare Git `receive.denyNonFastForwards`,
  `receive.denyDeletes` and `core.bare` must be true. Only the closed standard
  bare/receive config is allowed; includes, alternate object stores, external
  hook paths, special files and links reject. The complete config, hooks,
  refs and objects must be inaccessible to candidates.
- `{"kind":"https","token_file":"...","helper":"..."}`: endpoint is exactly
  `https://github.com/OWNER/REPOSITORY.git`. `helper` is this exact installed
  `git_broker.py`. The helper answers only an exact HTTPS host/repository
  credential request. The broker-only token never appears in argv, client
  files/environment, logs or responses. Redirects and ambient Git credentials
  or configuration are disabled.
- `{"kind":"ssh","key":"...","known_hosts":"..."}`: endpoint is exactly
  `ssh://git@github.com/OWNER/REPOSITORY.git`. `known_hosts` is root-provisioned;
  strict host checking is mandatory. No SSH agent, ambient SSH config, proxy
  command, forwarding, interactive prompt or password fallback is allowed.

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
| Canonical request JSON | 256 KiB |
| Signed hello/result | 8 KiB |
| Full pack | 16 MiB; 4,096 objects |
| Expanded objects | 2 MiB each; 64 MiB total |
| Plan/session lifetime | 30 seconds |
| Git/OpenSSL subprocess | 8 seconds; independent timeout cleanup |
| Subprocess output | 4 MiB, never replayed into responses/logs |
| Active request / queued connections | One / four |
| Nonce journal | 100,000 consumed entries; no replay-enabling eviction |

Packs are indexed with strict fsck in a new private bare object database;
decompression has CPU/address-space/file limits before object sizes can be
trusted. No candidate config, attributes, replacements, alternates, hooks,
template or worktree executes. Pack SHA-256 and object IDs identify the signed
external input/closure; they are not a committed source or ROM identity ledger.

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
They are **not evidence of protected three-principal installation or GitHub
authentication**. The required protected fixture fails (does not skip or
pretend success) when the OS boundary is unavailable. GitHub HTTPS/SSH
acceptance additionally needs a credentialed disposable repository with
actual authority/anchor protections, a provisioned broker, current trusted
signer and fresh exact plans. Do not fabricate that result from local mocks
or uncredentialed ls-remote.

Dependencies: existing signed-record, exact-repository and trusted external
installation contracts. Dependent: PR191/#178. Conflict: its provisional
unauthenticated broker/duplicate parser must be replaced; all other #178
safeguards remain. No gameplay/profile/save/resource interactions.

Rollback is a dedicated revert; dependent publication remains blocked rather
than falling back to an unauthenticated or generic Git transport.
