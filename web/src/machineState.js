export function getMachineViewState(runtime, runtimeError) {
  const uncertain = !runtime || !!runtimeError;
  const state = runtime?.state;
  const hasRuntimeId = Boolean(runtime?.runtime_id || runtime?.endpoint);
  const isUnknown = state === 'allocation_unknown';
  const isDisconnected = state === 'disconnected';
  const active = ['allocation_unknown', 'stopping', 'allocating', 'bootstrapping', 'syncing', 'starting', 'starting_comfy', 'ready'].includes(
    state,
  );
  const ready = state === 'ready';
  const canStart = !uncertain && !hasRuntimeId && ['off', 'error'].includes(state);
  const needsReset = !uncertain && (isUnknown || isDisconnected || (state === 'error' && hasRuntimeId));

  let statusSubtitle;
  if (ready) {
    statusSubtitle = 'ComfyUI 服务可用';
  } else if (uncertain) {
    statusSubtitle = '正在获取机器状态，请稍候…';
  } else if (isDisconnected) {
    statusSubtitle = runtime?.error || 'Colab 运行时连接已中断，请复位状态后重新开机';
  } else {
    statusSubtitle = runtime?.message || '按需启动你的绘图与视频计算环境';
  }

  const showMemory = !uncertain && !isDisconnected && state !== 'off' && runtime?.memory_used != null;
  const memoryPercent = showMemory && runtime?.memory_total
    ? Math.min(100, (100 * runtime.memory_used) / runtime.memory_total)
    : 0;

  return {
    uncertain,
    state,
    hasRuntimeId,
    isUnknown,
    isDisconnected,
    active,
    ready,
    canStart,
    needsReset,
    statusSubtitle,
    showMemory,
    memoryPercent,
  };
}
