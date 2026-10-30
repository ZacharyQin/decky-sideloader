# Sideloader tests

The current local acceptance completed `pnpm test:all`: 242 frontend tests, 85 current-Rust library tests plus 5 IPC tests, 18 actual Python/Rust bridge tests, 341 frozen-reference tests, 6 license-generator tests, both Clippy targets, release build, all 60 application groups, and the exact10-member runtime ZIP check. One public-service observation collector is intentionally ignored in the deterministic Rust run and was executed separately. Test counts are not coverage percentages.

[TEST_GAPS.md](TEST_GAPS.md) records the original12-category audit and the current completion boundary. G01–G11 now have the specified local matrices and combined acceptance. G12 records actual public-service and target-OS observations separately; fixture results do not establish current Loader loading, Steam layout/physical input, game performance or remote GitHub Actions.

## Commands

```sh
pnpm test:all
```

This runs TypeScript, frontend tests, Rust formatting, normal and private-target Clippy, current Rust, the production Python adapter/Rust IPC bridge, frozen0.5 reference, license generation, release build, all application groups and a temporary runtime package check. It preserves existing regular ZIPs in `dist`; symlinked ZIPs are refused. Final package checks require exactly10 members, valid CRC, matching ELF/build identity and absence of private-process configuration markers.

The library suite includes an actual60-second configured download-total-budget test with continuing25-second body progress. The application scheduler test also uses the actual Rust60-second interval and three profiles. Neither uses an accelerated production clock. Allow adequate command time; the complete acceptance takes several minutes.

Individual entries:

- `pnpm test`: frontend unit/component tests.
- `pnpm typecheck`: TypeScript.
- `pnpm test:rust`: current Rust, protocol and fixed differential corpus.
- `pnpm test:python`: build the actual debug core and run18 adapter/IPC tests.
- `pnpm test:reference`: explicitly listed frozen0.5 Python implementation; not current-Rust coverage.
- `pnpm test:licenses`: six generator/active-dependency/material checks.
- `pnpm build && pnpm test:e2e`: all60 application groups.
- `pnpm test:e2e --case discovery_crash_dependency --report test-results/dependency-crash.json`: one selected group.

E2E rejects stale frontend and Rust artifacts. The Rust source fingerprint includes test `.rs/.json` files, so a cfg(test)-only change also requires `pnpm build` before application acceptance. `test:e2e` builds an independently versioned0.6.1 candidate when the self-update groups need it; ownership markers and source fingerprints guard candidate-cache replacement. CI uses the same unified command and retains JSON/log evidence on failure. A local run is not evidence that remote Actions ran.

## Coverage by execution layer

| Behavior | Entry | Actual execution boundary |
| --- | --- | --- |
| React UI/controllers, cancellation, late results and remount | `test` | Real components with API/host mocks. |
| Release/cache, search/multiplugin, mapping/ownership, batch/scheduler, ZIP/download/dependencies, nonempty backup/recovery, self-update and persistence faults | `test:rust` | Current Rust with actual temporary files, sockets and isolated fault children. The1317 differential inputs cover18 pure-function categories, not runtime flow coverage. |
| ZIP/CRC/count/expansion and framed protocol limits/concurrency/EOF | `test:rust`, `test:python` | Valid-baseline single-fault archives, actual parser/writer and real child IPC. |
| Diagnostic expiry/interface loss, HEAD/quota reservations, RST/slow clients and stop/preparation races | `test:rust` | Actual listener; controlled deadlines/interfaces/gates exist only in cfg(test). |
| Adapter ELF/hash/handshake, saturation/callback exemptions, persistence, crash and parent exit | `test:python` | Unmodified production Python adapter with a real debug core. |
| Discovery/search/versions/downloads/install/uninstall/ownership/checks/diagnostics/self-update/recovery | `test:e2e` | Actual built bundle, React renderer, production Python adapter, actual stdio and Rust entry/Engine. GitHub, native Loader/frozen-service boundaries are strict fixtures. |
| Original12 ready-install cases and old8-combination self-update identity recovery | Original E2E selectors | Authored starting records retained as focused tests; they are not used to prove complete version or self-update preparation. |
| Real public TLS/API/asset service observations | Explicit ignored collector | Actual production Transport/reqwest, no fixture origin, no environment proxy and unchanged URL/redirect/TLS rules. Availability is recorded per API/asset result. |
| Target OS/Python/ELF/adapter compatibility | `scripts/test-device.py` | Actual target machine and release package in a newly owned private profile; SDK boundary isolated, no Loader/plugin deployment. |

Application collection is fixed at60 unique groups: original16, version13, ownership/binding6, crash7, self-update9, discovery6, checks2 and diagnostic1. Missing/duplicate collection fails. A scheduler group may use multiple private profiles; identity combinations, persistence subcases and assertions are not added to the top-level group count.

## Actual application flows

Version workflows start with an empty operation and the real version page: upgrade, same-version reinstall, downgrade,1→2→1→2, uninstall/reinstall and cancellation. They verify the corresponding native types, immutable dispatched archive, actual installed files, persisted binding and actual nonempty Rust-generated settings/data backup. Restarted preparation requires a new explicit native confirmation and follows that entry's defaulttype4 contract.

Discovery explicitly chooses a repository subplugin and ZIP rather than accepting the first result. The Beta input ZIP lacks its two binaries: Rust must actually download both, verify fixed independent SHA values, merge/reinspect and preserve the source ZIP, comment and0755 members. Bad second dependency, truncated tree and failed second release page cannot yield partial successful results or native dispatch.

Self-update discovers and downloads a genuinely different0.6.1 ZIP/ELF/bundle, dispatches replacement and boots the new release core. PID/boot, executable SHA, backend/frontend fingerprints and actual file replacement are checked. The new program with an old frontend remains incompatible/verifying; completion requires the actual candidate bundle. Cancel/failure/restart cases do not fabricate installation or replay a request.

Eight actual SIGKILL phase cases exist across workflow/discovery inputs: main body after64bytes, dependency2 after4bytes, first backup content block, ready, native awaiting, native installing, native uninstalling and durable stage=verified before result closure. They verify connection EOF, a genuinely new core/frontend, retained evidence, explicit recovery where needed, no unknown native replay and no old-instance late RPC. Backup reuse preserves original bytes/SHA/age. Interrupted dependency work is reclaimed only with matching source/operation/count ownership marker, safe UID/mode/single-link known members; unknown content and links remain untouched.

The batch groups persist one update and one independent plugin error. Scheduled checks use the real60-second Rust scheduler, not a frontend `check_updates` substitute. Remount notifies once; unmounted/no native-management responder has no plugin eligibility and no toast; notifications-off still saves the actual report/event.

Diagnostic UI uses a snapshot of an actual private IPv4 interface taken before the private test process enters its sandbox. Production address/bind checks remain active. Real UI export/QR/copy, HTTP HEAD/GET, exact pickup URL/count/headers, complete ZIP manifest/member SHA/permissions/privacy and explicit stop/listener closure are verified. This is a same-host connection to its actual private interface, not a physical remote-LAN-client test. The discarded loopback application approach is not used.

## Fixture contracts and recovery evidence

`e2e/harness.mjs` loads the actual module in a Node VM with React test renderer. Native host fixtures write reviewed fixed archive contents and emit actual Loader event shapes; they do not replace business RPCs with success DTOs. Originally focused baseline/ready/identity groups use the distributed release core with non-Unix sockets denied. Network flows use the private Cargo test target with a strictly local HTTP origin; canonical URL policy is evaluated before the test-origin rewrite.

Only cfg(test) code reads `.sideloader-e2e-origin`, `.sideloader-e2e-share-addresses` or `.sideloader-e2e-backup-gate`. The last marker names a bounded Unix checkpoint (`backup-first-chunk` or `install-verified`). Release/package checks require all three markers absent. The binary/metadata/FD execution/handshake checks in the Python adapter are unchanged for both release and private targets.

HTTP fixtures require exact GET/HTTP1.1/Host/Accept/identity encoding/User-Agent/header whitelist, canonical URL, Range and consumption counts. Unknown, overconsumed, missing or unconsumed scripted replies fail. Current Rust transport cases cover four routes, API status policy, partial fallback without mixed bytes, allowed1/5 redirects and sixth rejection, unsafe targets, header/body inactivity deadlines, the actual total budget and frozen in-flight routing. Header/inactivity timer tests may advance Tokio's test clock; the total-budget case uses the production std monotonic clock for an actual minute.

Native effects and dispatch/confirm/cancel history are append-only. Independent installed-file/source/backup SHA, ZIP CRC/members/modes, protected-input hashes and request/event identity are required. Confirmation checks wait for the appropriate real durable acknowledgement; transient stages are not terminal evidence. The verified-result SIGKILL gate runs only after the actual installed-file verification has persisted stage=verified, before publication/closure, rather than authoring a recovery journal.

Eight durable-write fault subcases run in isolated children: install-submit/prompt/start/result, self-submit, ownership-intent/result and backup-result. They briefly constrain only the selected child's real atomic write with RLIMIT_FSIZE to produce EFBIG, then restore limits/signals. Old durable bytes/permissions/native effects survive, temporary output is checked and explicit retry must succeed. These are subcases in one library test, not eight extra library tests.

All app waits are bounded and teardown checks child exits/PIDs, RPC settlement, subscriptions, VM timers, HTTP clients/listeners and checkpoint connections. One controlled in-flight native-uninstall case may temporarily retain its single actual120-second watchdog; completion must still release it and final teardown requires zero timers. Oracle corruption controls prove that the result/consumption/collection checks reject deliberately corrupted observations; this is not an arbitrary product-source mutation campaign.

## Explicit environment probes

Public service collector:

```sh
SIDELOADER_REAL_SERVICE_REPORT="$PWD/test-results/live-services.json" cargo test --offline --locked --manifest-path backend-rs/Cargo.toml --lib collect_public_service_transport_observations -- --ignored --nocapture
```

`e2e/fixtures/real-service.json` freezes a public CSSLoader v2.1.2 URL/size and independent Python HTTPS byte reference. A successful collector means observations were recorded without content mismatch; it does not mean every remote service was available. Current observations: all four asset transfers matched188377bytes and the fixed SHA, while API requests were rate_limited. No anonymous limit was bypassed and no token was requested.

Private target probe, after local acceptance:

```sh
SIDELOADER_PACKAGE_OUTPUT="$PWD/test-results/device-smoke.zip" pnpm package-plugin
python3 -B scripts/test-device.py --ssh-target sd --package test-results/device-smoke.zip --report test-results/device-smoke.json
```

The wrapper refuses any package member differing from current local runtime inputs. SSH keeps strict host-key checking and existing account authentication. The remote runner creates a unique0700 owned directory, verifies ZIP/ELF metadata, imports the package's production Python adapter, executes the release core, checks handshake/ping/settings/null operation and clean shutdown, then removes only the verified-owned stopped profile. Timeout/unexpected entries are retained with an exact probe identifier. It does not edit the installed plugin, Loader, user settings or global services.

The current package passed this probe on SteamOS3.9.5/x86_64/glibc2.43/Python3.14.6 with child exit0, zero pending RPCs and owned-profile removal. Actual installed-plugin loading, Steam layouts/physical input/game resources and remote Actions remain separate evidence requirements.

## Updating inputs

Keep source/fixtures in version-control scope and outputs/caches in ignored locations. Write independent expected behavior first. Never refresh hashes from the application output under test. Add explicit route/Range/count and native effect contracts. Preserve failed evidence and distinguish product regressions from fixture mistakes. Use a named case to reproduce, then run the complete suite after relevant source changes. Keep runtime packaging an explicit10-member manifest; Git ignore rules do not define the distribution.
