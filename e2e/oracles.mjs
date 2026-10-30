import assert from 'node:assert/strict';

export function assertInstallation(expected, actual) {
  assert.equal(actual.operation.stage, expected.stage, `${expected.id}: terminal/blocked stage`);
  assert.equal(actual.operation.outcome, expected.outcome, `${expected.id}: evidence-based outcome`);
  for (const [kind, count] of Object.entries({dispatch: expected.dispatches, confirm: expected.confirms, cancel: expected.cancels}))
    assert.equal(actual.history.filter(row => row.kind === kind).length, count, `${expected.id}: exact ${kind} consumption`);
  assert.deepEqual(actual.protectedHashes, actual.expectedProtectedHashes, `${expected.id}: preserved settings/data`);
  if (expected.outcome === 'success') {
    assert.deepEqual(actual.installedHashes, actual.expectedInstalledHashes, `${expected.id}: independently hashed installed files`);
    assert.equal(actual.operation.binding_saved, true, `${expected.id}: repository binding saved`);
    assert.equal(actual.artifactExists, false, `${expected.id}: successful artifact cleanup`);
    if (expected.package === 'overwrite') assert.equal(actual.backupMatches, true, `${expected.id}: retained backup identity`);
  } else {
    assert.equal(actual.artifactExists, true, `${expected.id}: unconfirmed artifact retained`);
    if (expected.confirms === 0) assert.deepEqual(actual.originalProgramAfter, actual.originalProgramBefore, `${expected.id}: no native file write`);
  }
}

export function assertSelfUpdate(expected, actual) {
  assert.equal(actual.operation.stage, expected.stage, `${expected.id}: cross-instance self-update stage`);
  assert.equal(actual.frontend_compatible, expected.compatible, `${expected.id}: frontend identity`);
}

export function assertHttpTrace(expected, actual) {
  assert.deepEqual(actual.counts, expected, 'independent exact HTTP consumption');
  assert.deepEqual(actual.required, expected, 'fixture route contract has not drifted');
  assert.deepEqual(actual.mismatches, {});
  assert.deepEqual(actual.errors, []);
}

export function assertCoverage(expected, actual) {
  assert.deepEqual([...actual].sort(), [...expected].sort(), 'fixture case collection must be exact');
}

export function exerciseOracles(cases) {
  const expected = cases.find(row => row.id === 'fresh_success');
  const hashes = {'main.py': 'fixed-golden-hash'};
  const good = {operation: {stage: 'closed', outcome: 'success', binding_saved: true}, history: [{kind:'dispatch'}, {kind:'confirm'}],
    protectedHashes: hashes, expectedProtectedHashes: hashes, installedHashes: hashes, expectedInstalledHashes: hashes, artifactExists: false};
  assertInstallation(expected, good);
  const mutations = [
    ['wrong-terminal-stage', {...good, operation: {...good.operation, stage:'unknown', outcome:null}}],
    ['duplicate-dispatch', {...good, history:[...good.history, {kind:'dispatch'}]}],
    ['wrong-installed-byte', {...good, installedHashes:{'main.py':'different-hash'}}],
    ['destroyed-user-data', {...good, protectedHashes:{'main.py':'changed'}}],
    ['uncleaned-artifact', {...good, artifactExists:true}],
  ];
  for (const [name, mutated] of mutations) assert.throws(() => assertInstallation(expected, mutated), assert.AssertionError, `oracle failed to reject ${name}`);
  assert.throws(() => assertInstallation(cases.find(row=>row.id==='wrong_hash'), good), assert.AssertionError, 'unproven success must fail');
  assert.throws(() => assertHttpTrace({'fixed-route':1}, {counts:{},required:{'fixed-route':1},mismatches:{},errors:[]}), assert.AssertionError, 'missing HTTP consumption must fail even if report claims no mismatches');
  assert.throws(() => assertCoverage(cases.map(row => row.id), []), assert.AssertionError);
  assert.throws(() => assertSelfUpdate({id:'stale-frontend', stage:'verifying', compatible:false}, {operation:{stage:'completed'},frontend_compatible:false}), assert.AssertionError);
  return {rejected_install_result_corruptions: mutations.map(([name]) => name), rejected_unproven_success:true,
    rejected_missing_http_consumption:true, rejected_empty_collection:true, rejected_stale_frontend_completion:true};
}
