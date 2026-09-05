# Authenticated Git publication broker

Issue [#205](https://github.com/laqieer/fireemblem8-expansion/issues/205)
adds a framework capability for publishing one workflow-pilot authority ref
and its anchor without placing GitHub credentials or protected Git state in a
candidate checkout. It is a prerequisite for issue #178 and replaces no
merged publication path. It has no ROM, RAM, save, configuration-identity,
generated-data, localization, gameplay, modern-build, or archival-lane impact.

## Trust boundary

The candidate-side client and the broker are
[`scripts/workflow_pilot/git_publication_broker.py`](../scripts/workflow_pilot/git_publication_broker.py).
They are two roles, not one trust domain:

- the broker runs under a separate **outer host UID**. A user namespace that
  maps the broker back to the candidate's host UID is not authority;
- an external trusted capability issuer creates a fresh unnamed `AF_UNIX`
  `SOCK_STREAM` socket pair, launches exactly one broker operation with one end,
  and gives the other inherited descriptor to exactly one candidate client;
- the same issuer passes the broker a canonical, authority-signed, fully
  write/grow/shrink/seal-locked memfd that binds that connection to one
  installation, repository, issue, operation, plan identity, nonce, and
  lifetime;
- the candidate cannot name, reconnect to, or replace that capability;
- Linux `SO_PEERCRED` must report the installed capability-issuer UID at both
  ends. That issuer is distinct from both broker and candidate principals;
  same-UID, pathname, and abstract sockets fail closed;
- the client verifies every request-bound response with the externally
  installed Ed25519 broker public key.

A filesystem socket protected only by mode bits is not this boundary.
An abstract socket is forbidden. A test that calls the library with peer
checking disabled proves protocol behavior and response signatures only; it
does not claim same-UID isolation.

The production entry points require isolated Python startup:

```text
/usr/bin/python3 -I /opt/workflow-pilot-broker/scripts/workflow_pilot/isolated_launcher.py \
  git-broker-serve --installation /etc/workflow-pilot/broker.json \
  --connection-fd 3 --capability-fd 4

/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py \
  git-broker-publish --installation /etc/workflow-pilot/client.json \
  --connection-fd 3 --issue 205 \
  --pack build/test-artifacts/authority.pack

/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py \
  git-broker-reconcile --installation /etc/workflow-pilot/client.json \
  --connection-fd 3 --issue 205
```

The external capability issuer, not candidate code, owns socket-pair creation
before either endpoint is transferred. Unix socket-pair credentials describe
that creator, so both installations pin its separate UID; they do not pretend
that a transferred descriptor changes peer credentials. The issuer also owns
the descriptor allowlist, UID transition, launch deadline, and process teardown.
It must close both descriptors after the one operation. Do not put a broker
socket path, private key, token, askpass output, or SSH-agent descriptor in the
candidate environment.
The broker launcher/package and schemas under `/opt/workflow-pilot-broker` are
an immutable root-owned installation of the reviewed repository code, never
the candidate checkout. The client may run from the candidate tree because it
holds no credential or server authority.

Build and install the reviewed native boundary under the same protected tree:

```text
/usr/bin/cc -std=c11 -O2 -Wall -Wextra -Werror \
  scripts/workflow_pilot/deadline_exec.c \
  -o /opt/workflow-pilot-broker/bin/workflow-pilot-deadline-exec
chown root:root /opt/workflow-pilot-broker/bin/workflow-pilot-deadline-exec
chmod 0755 /opt/workflow-pilot-broker/bin/workflow-pilot-deadline-exec
```

The candidate names no plan. Supplying another issue or operation produces a
signed rejection before the object pack is read. Another otherwise valid plan
in the broker's protected store is unusable through that connection.

## Closed publication contract

The external installation authority writes a signed plan conforming to
[`git_publication_plan.schema.json`](../scripts/workflow_pilot/git_publication_plan.schema.json).
The plan identity is the SHA-256 of its canonical JSON including its signature.
The signature covers canonical JSON without the `signature` member under the
`workflow-pilot-git-publication-plan-v1` domain.
The connection capability conforms to
[`git_publication_capability.schema.json`](../scripts/workflow_pilot/git_publication_capability.schema.json)
and is independently signed under the
`workflow-pilot-git-publication-capability-v1` domain.
Signed ACK/final fields and durable outcome names are published in
[`git_publication_result.schema.json`](../scripts/workflow_pilot/git_publication_result.schema.json).

The broker independently verifies all of the following:

- installation identity, repository, issue, canonical endpoint, signer, actor,
  key ID, Ed25519 signature, operation, nonce, positive sequence, issued time,
  expiry, and maximum lifetime;
- exactly
  `refs/heads/workflow-pilot/issue-<issue>/authority` and
  `refs/tags/workflow-pilot/issue-<issue>/anchor`;
- exact expected old and new lowercase 40-hex object IDs;
- a bounded self-contained pack whose SHA-256, byte length, indexed object set,
  and complete reachability closure exactly equal the signed sorted object
  list, with both new targets resolving to commits;
- exact remote old-ref state before an atomic two-ref force-with-lease push and
  exact remote readback afterward.

The wire protocol uses bounded length-prefixed canonical JSON. The client sends
only a request header, then drains and verifies a signed `ack`. It returns a
small request/plan-bound `continue` frame and sends pack bytes only after
`status=ready`. A signed final result repeats the request,
capability, plan, deadline, broker process, and installation binding. On a
write failure the client still drains and authenticates the bounded final
rejection; raw `BrokenPipe` is never success evidence.

The effective deadline is the minimum of the request, capability, and plan
expiries. One absolute wall/monotonic deadline is carried through signature
validation, credential readiness, remote reads, replay validation, pack
index/closure checks, hook execution, push, and exact readback. Every child
timeout is clamped to its remaining duration, and the broker never starts a
push after that deadline.

A network server is independent once an authenticated atomic push is
transmitted. Killing local Git cannot prove that GitHub did not commit after
the deadline. Therefore any timeout, disconnect, deadline crossing, failed
post-push readback, or ambiguous push result is `indeterminate`: the nonce and
sequence remain permanently consumed and quarantined. The broker performs a
new bounded exact two-ref readback:

| Readback | Durable result |
| --- | --- |
| Exact planned authority and anchor | `committed-late` |
| Exact signed old pair on broker-controlled local remote, plus durable proof that its exact receive-pack process group terminated | `safe-failed` |
| Exact signed old pair on HTTPS/SSH/network transport | `indeterminate`; GitHub provides no authoritative transaction-termination proof |
| Mixed or any other observed pair | `security-hold` |
| Readback/protected authority unavailable | `indeterminate`; only a new trusted `reconcile` capability may retry readback |

Reconciliation never retransmits a push. Local protected hooks may enforce the
exported deadline, but neither code nor documentation claims that a remote
server cannot move refs after locally observed expiry.

Every signed final publication response is parsed into a closed typed outcome.
`committed-late` is successful. `safe-failed` is non-success, is available
only for the protected-local transport with exact terminated-receive-pack
proof, and permits only a new higher-sequence plan. `security-hold` is a
non-success incident and preserves the exact observed authority and anchor
names plus nullable OIDs. `indeterminate` is non-success, may preserve the
currently observed exact old pair, and requires another trusted reconciliation
capability. Network old refs never become `safe-failed` automatically. The
client validates the response
signature, request, plan, repository, issue-derived ref names, and each OID
before returning any outcome. Results and the replay journal bind
`transport` (`network` or `protected-local`) and `termination_proof`
(`not-required`, `protected-receive-pack-terminated`, or `unavailable`).
The reconciliation CLI emits canonical JSON containing `outcome`, `refs`,
`transport`, `termination_proof`, and `retry_disposition`; exit statuses are
`0`, `3`, `4`, and `5` respectively.

`master`, other heads, other tags, deletions, wildcard refspecs, arbitrary Git
commands, thin/missing closures, extra objects, and a third ref are not
representable. The server remains authoritative. A local protected remote is
opened through no-follow directory/file descriptors; Git uses the descriptor
path rather than a replaceable pathname. Config and the complete hook tree are
descriptor-hashed, external hook/config execution seams and object alternates
are forbidden, and every nested hooks/config/refs/objects entry is recursively
owner/type/mode checked. Mutable refs/objects storage must be broker-owned;
candidate ownership, writable modes, symlinks, special files, or path swaps
fail or remain outside the descriptor-bound server.

The required
[`signed_schema.py`](../scripts/workflow_pilot/signed_schema.py) entry point
registers the calendar-aware `rfc3339-utc-second` format and is invoked by
every normal plan and capability validator. The shared signed-record parser accepts only
`YYYY-MM-DDTHH:MM:SSZ`. It constructs the UTC calendar fields directly, so
invalid dates, year `0000`, non-leap February 29, April 31, hour `24`, offsets,
lowercase separators, and fractional seconds are rejected identically on every
supported Python version. The schemas carry both `format: date-time` and the
named `rfc3339-utc-second` extension; the repository's semantic schema
consumer calls the same calendar constructor as the record parser instead of
treating a regex as date validation.

## Broker installation

Install the broker configuration, plan store, state directory, signing private
key, authority public keys, HTTPS credential/askpass or SSH-agent socket, and
any protected local integration remote under the broker principal or root.
No path component may be a symlink or group/world writable. The client
manifest and broker public key must be rooted outside every candidate-writable
directory and owned by an external principal.

The broker JSON is a closed object with these fields:

| Field | Contract |
| --- | --- |
| `schema_version`, `protocol` | `1` and `workflow-pilot-authenticated-git-broker-v1`. |
| `installation_id` | Unique 64-lowercase-hex identity. Copying a plan to another installation fails. |
| `repository`, `endpoint` | Exact `owner/repository` and canonical HTTPS, `ssh://git@...`, or test-only absolute `file://` destination. URL credentials are forbidden. |
| `expected_capability_uid` | Trusted socket-pair creator UID, different from the broker effective UID. |
| `candidate_uid` | Candidate principal, different from both broker and capability issuer and forbidden throughout protected authority trees. |
| `broker_key_id`, `broker_private_key` | External response identity and protected Ed25519 private-key path. |
| `deadline_exec` | Root/broker-owned compiled helper from `deadline_exec.c`; checks immutable wall and monotonic deadlines immediately before `execve`. |
| `plan_signers` | Nonempty key-ID map to protected Ed25519 public key plus exact signer and actor strings. |
| `plan_store`, `state_directory` | Protected signed plans and replay/process state. |
| `authentication` | One of `https-askpass`, `ssh-agent`, or test-only `local-test`. |
| `protected_remote` | `null` for a production network remote; required device/inode/config/hook identities for a local test remote. |
| limits | Positive `pack_max_bytes`, `operation_timeout_seconds`, `reconciliation_timeout_seconds`, and `plan_lifetime_seconds`. |
| `test_only` | `false` in production. |

The committed redacted structures are
[`git_broker_service.example.json`](../scripts/workflow_pilot/tests/fixtures/git_broker_service.example.json)
and
[`git_broker_client.example.json`](../scripts/workflow_pilot/tests/fixtures/git_broker_client.example.json).
They contain paths and public identities only, never usable keys or tokens.

For HTTPS, `authentication` contains `mode`, executable `askpass`,
`credential_file`, and nullable `ca_file`. The askpass executable reads the
credential only inside the broker process tree. For SSH it contains `mode`,
`agent_socket`, and a pinned `ssh_config`; the agent and host-key policy remain
outside the candidate. Git receives a minimal scrubbed environment and output
is never returned. The reviewed native deadline helper installs
`PR_SET_PDEATHSIG`, checks the expected parent, applies core/file limits,
rechecks immutable absolute wall and monotonic deadlines immediately before
`execve`, and reports whether Git was executed. Python uses no `preexec_fn`,
recomputes remaining time after `Popen` and immediately before `communicate`,
and sends every post-`Popen` exception through one marker classifier. A closed
pre-exec helper with no exec marker is non-transmitted; an observed or unknown
marker is conservatively timeout/indeterminate. A second native watchdog kills
the full process group if the broker dies. Git, OpenSSL signing/verification,
askpass, SSH-agent probes, credential checks, and every other broker child use
this same helper and watchdog—there is no direct secret-bearing
`start_new_session` path.

The client JSON contains only `schema_version`, `protocol`, `installation_id`,
`repository`, `endpoint`, `expected_broker_uid`,
`expected_capability_uid`, `broker_key_id`, `broker_public_key`,
`deadline_exec`,
`pack_max_bytes`, `operation_timeout_seconds`, and externally installed
`test_only` (`false` in production).
Validate deployment before launching candidate work:

```text
# The trusted issuer first launches a preflight-bound broker with its sealed
# capability FD, separate service UID/namespace, and unnamed connection FD.
/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py \
  git-broker-preflight --installation /etc/workflow-pilot/client.json \
  --connection-fd 3 --issue 205
```

`expected_broker_uid` records the separately deployed service principal. The
externally installed signing key authenticates the broker response because
transferred socket-pair credentials authenticate the capability issuer, not
the eventual descriptor holder. Preflight is a real signed non-publication
exchange: it verifies the sealed plan binding, service response key, observed
broker PID/outer-host-UID through a live pidfd and `/proc`, inability to signal
or ptrace the service, configuration, credential/agent readiness, protected
remote, and exact current refs. It then performs an exact atomic two-ref
`git push --dry-run` through receive-pack and verifies refs remain unchanged,
so public anonymous reads, read-only principals, expired tokens, and read-only
SSH agent identities do not pass as write readiness. It
returns only `ready`, never an expected UID copied from a manifest. Missing
service, wrong UID/namespace/key, candidate-owned installation, writable or
symlinked authority, stale refs, or unavailable credentials fail closed.
There is no local fallback.

## Replay state, lifecycle, and recovery

The broker holds an exclusive journal lock from nonce reservation through
publication and readback. Each durable append is hash-chained, `fsync`ed, and
bound to the installation identity; a separately replaced and directory-
`fsync`ed anchor must match the chain tip. Nonce reuse, non-increasing sequence,
concurrent copies, restart replay, a copied installation, and restoring an
older journal without its anchor fail closed. The signed expected old refs add
the remote compare-and-swap boundary.

Start no persistent candidate-accessible daemon. The trusted coordinator
starts one broker process per capability. Stop by closing the capability and
terminating that exact process; parent death and bounded Git timeouts clean the
Git process group. A disconnect may burn a reserved nonce and must never be
retried with the same plan.

If a crash leaves the journal and anchor inconsistent, or protected remote
identity changes, do not edit either record in place. Reconcile the exact
remote refs under the broker administrator, provision a new installation ID
and empty protected state, rotate the response key if compromise is possible,
and issue a new higher-sequence signed plan with the observed old refs.
Rollback protection for both state and anchor together is a deployment
responsibility: place them in the service principal's rollback-protected
storage and never restore a snapshot as the same installation.

Rotate a GitHub token, askpass file, SSH agent, or response key by draining all
one-shot capabilities, replacing the protected material atomically, updating
the root-owned client public identity when needed, changing the installation
ID, and issuing fresh plans. Never log credential probes or include secrets in
service arguments, environment exported to the candidate, responses, test
artifacts, or crash dumps.

## Local validation and limitations

Run:

```text
python3 -m unittest scripts.workflow_pilot.tests.test_git_publication_broker -v
/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py reporter-tests
/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py \
  validate-signed-record --schema plan --input <signed-plan.json>
python3 scripts/check_docs.py --check
```

The focused suite creates all files beneath
`build/test-artifacts/git-publication-broker`, starts a signed one-shot broker
process, exercises an actual TLS smart-HTTP Git server and askpass challenge,
and removes the fixture. A mapped namespace with the same outer UID is a
negative control. When passwordless `sudo` is available, the integration test
uses root as capability issuer, `nobody` as broker, and `daemon` as candidate;
it runs the real installation loaders and isolated serve/preflight/publish
CLIs, proves file/signal/ptrace isolation, and performs the receive-pack dry
run plus publication. The hosted `host-tests` workflow runs this test on its
sudo-capable runner; local hosts without passwordless sudo report one explicit
skip. Operators must still provide the real separate principal,
trusted coordinator, protected installation paths, GitHub repository
permissions/rules, TLS/SSH host identity, and rollback-resistant service
storage. Linux deployment must expose signed broker PID/status/UID mapping and
pidfd observation to the unprivileged candidate while denying signals,
`/proc/<pid>/mem`, ptrace, and every broker-owned file. Hosted integration
requires passwordless sudo plus distinct `nobody` and `daemon` accounts.

No reasoning agent should wait for CI on behalf of this service. The delivery
coordinator retains the repository's existing direct bounded workflow watcher
contract.

## Dependencies and rollback

| Relationship | Contract |
| --- | --- |
| Dependencies | Existing workflow-pilot canonical JSON/signed-record timestamp, exact repository, Git object, and external installation authority conventions. |
| Dependents | Issue #178 and PR #191 authority publication. |
| Conflicts | PR #191's provisional abstract same-UID test broker; it must use this contract rather than retain that transport. |
| Other feature/profile conflicts | None. |

Rollback is a normal revert of issue #205. Dependents remain blocked rather
than falling back to unauthenticated or generic privileged Git transport.
