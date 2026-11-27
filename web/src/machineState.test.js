import test from 'node:test';
import assert from 'node:assert/strict';
import { getMachineViewState } from './machineState.js';

test('disconnected runtime hides stale message/memory and requires reset instead of deadlocked start', () => {
  const vm = getMachineViewState({
    state: 'disconnected',
    runtime_id: 'gpu-g4-stale',
    endpoint: 'gpu-g4-stale',
    message: 'ComfyUI已就绪',
    error: 'Colab运行时已回收或释放',
    memory_used: 21071134720,
    memory_total: 102641958912,
  });
  assert.equal(vm.uncertain, false);
  assert.equal(vm.isDisconnected, true);
  assert.equal(vm.active, false);
  assert.equal(vm.canStart, false);
  assert.equal(vm.needsReset, true);
  assert.equal(vm.statusSubtitle, 'Colab运行时已回收或释放');
  assert.equal(vm.showMemory, false);
  assert.equal(vm.memoryPercent, 0);
});

test('error state with retained runtime_id enables reset, while clean error allows start', () => {
  const retained = getMachineViewState({
    state: 'error',
    runtime_id: 'gpu-g4-broken',
    error: 'ComfyUI初始化失败，请检查启动日志',
  });
  assert.equal(retained.canStart, false);
  assert.equal(retained.needsReset, true);
  assert.equal(retained.active, false);

  const clean = getMachineViewState({
    state: 'error',
    runtime_id: null,
    message: 'Colab暂时无可用实例',
  });
  assert.equal(clean.canStart, true);
  assert.equal(clean.needsReset, false);
  assert.equal(clean.statusSubtitle, 'Colab暂时无可用实例');
});

test('allocation_unknown, off, and ready states map cleanly', () => {
  const unknown = getMachineViewState({ state: 'allocation_unknown', endpoint: null });
  assert.equal(unknown.active, true);
  assert.equal(unknown.isUnknown, true);
  assert.equal(unknown.needsReset, true);
  assert.equal(unknown.canStart, false);

  const off = getMachineViewState({ state: 'off', memory_used: 1234 });
  assert.equal(off.active, false);
  assert.equal(off.canStart, true);
  assert.equal(off.needsReset, false);
  assert.equal(off.showMemory, false);

  const ready = getMachineViewState({
    state: 'ready',
    runtime_id: 'gpu-g4-ok',
    memory_used: 25,
    memory_total: 100,
  });
  assert.equal(ready.ready, true);
  assert.equal(ready.active, true);
  assert.equal(ready.needsReset, false);
  assert.equal(ready.statusSubtitle, 'ComfyUI 服务可用');
  assert.equal(ready.showMemory, true);
  assert.equal(ready.memoryPercent, 25);
});
