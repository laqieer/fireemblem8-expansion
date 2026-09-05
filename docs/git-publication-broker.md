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

- the broker runs under a separate OS principal or stronger host/namespace
  boundary;
- an external trusted capability issuer creates a fresh unnamed `AF_UNIX`
  `SOCK_STREAM` socket pair, launches exactly one broker operation with one end,
  and gives the other inherited descriptor to exactly one candidate client;
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
/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py \
  git-broker-serve --installation /etc/workflow-pilot/broker.json \
  --connection-fd 3

/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py \
  git-broker-publish --installation /etc/workflow-pilot/client.json \
  --connection-fd 3 --plan-identity <64-lowercase-hex> \
  --pack build/test-artifacts/authority.pack
```

The external capability issuer, not candidate code, owns socket-pair creation
before either endpoint is transferred. Unix socket-pair credentials describe
that creator, so both installations pin its separate UID; they do not pretend
that a transferred descriptor changes peer credentials. The issuer also owns
the descriptor allowlist, UID transition, launch deadline, and process teardown.
It must close both descriptors after the one operation. Do not put a broker
socket path, private key, token, askpass output, or SSH-agent descriptor in the
candidate environment.

## Closed publication contract

The external installation authority writes a signed plan conforming to
[`git_publication_plan.schema.json`](../scripts/workflow_pilot/git_publication_plan.schema.json).
The plan identity is the SHA-256 of its canonical JSON including its signature.
The signature covers canonical JSON without the `signature` member under the
`workflow-pilot-git-publication-plan-v1` domain.

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

`master`, other heads, other tags, deletions, wildcard refspecs, arbitrary Git
commands, thin/missing closures, extra objects, and a third ref are not
representable. The server remains authoritative: protected receive hooks run,
and the broker fingerprints the local test remote's directory/object
identities plus config and hooks before and after publication.

The shared signed-record parser accepts only
`YYYY-MM-DDTHH:MM:SSZ`. It constructs the UTC calendar fields directly, so
invalid dates, hour `24`, offsets, lowercase separators, and fractional seconds
are rejected identically on every supported Python version.

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
| `broker_key_id`, `broker_private_key` | External response identity and protected Ed25519 private-key path. |
| `plan_signers` | Nonempty key-ID map to protected Ed25519 public key plus exact signer and actor strings. |
| `plan_store`, `state_directory` | Protected signed plans and replay/process state. |
| `authentication` | One of `https-askpass`, `ssh-agent`, or test-only `local-test`. |
| `protected_remote` | `null` for a production network remote; required device/inode/config/hook identities for a local test remote. |
| limits | Positive `pack_max_bytes`, `operation_timeout_seconds`, and `plan_lifetime_seconds`. |
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
outside the candidate. Git receives a minimal scrubbed environment, core dumps
are disabled, output is never returned, and a broker-death watchdog kills the
entire Git process group.

The client JSON contains only `schema_version`, `protocol`, `installation_id`,
`repository`, `endpoint`, `expected_broker_uid`,
`expected_capability_uid`, `broker_key_id`, `broker_public_key`,
`pack_max_bytes`, and `operation_timeout_seconds`.
Validate deployment before launching candidate work:

```text
/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py \
  git-broker-preflight --installation /etc/workflow-pilot/client.json
```

`expected_broker_uid` records the separately deployed service principal. The
externally installed signing key authenticates the broker response because
transferred socket-pair credentials authenticate the capability issuer, not
the eventual descriptor holder. Preflight fails when the installation is candidate-owned, under a
candidate-owned path, writable by group/other, malformed, same-UID, symlinked,
or not canonical. Absence of an externally installed broker is a hard failure;
there is no local fallback.

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
python3 scripts/check_docs.py --check
```

The focused suite creates all files beneath
`build/test-artifacts/git-publication-broker`, starts a signed one-shot broker
process, exercises an actual TLS smart-HTTP Git server and askpass challenge,
and removes the fixture. It also proves that production preflight rejects the
same-UID test layout. Operators must still provide the real separate principal,
trusted coordinator, protected installation paths, GitHub repository
permissions/rules, TLS/SSH host identity, and rollback-resistant service
storage.

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
