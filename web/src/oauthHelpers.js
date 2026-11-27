export function parseGoogleClientJson(rawText) {
  let data;
  try {
    data = typeof rawText === 'string' ? JSON.parse(rawText) : rawText;
  } catch {
    throw new Error('客户端 JSON 格式无效，请检查内容是否为完整 JSON');
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    throw new Error('OAuth 客户端配置必须是 JSON 对象');
  }
  const kind = data.installed ? 'installed' : data.web ? 'web' : 'flat';
  const section = data.installed || data.web || data;
  if (!section || typeof section !== 'object') {
    throw new Error('找不到有效的客户端配置字段');
  }
  const client_id = String(section.client_id || '').trim();
  const client_secret = String(section.client_secret || '').trim();
  const redirects = Array.isArray(section.redirect_uris)
    ? section.redirect_uris.map(v => String(v || '').trim()).filter(Boolean)
    : [];
  if (!client_id) {
    throw new Error('JSON 中缺少 client_id');
  }
  return {
    kind,
    client_id,
    client_secret,
    redirect_uri: redirects[0] || '',
    redirect_uris: redirects,
  };
}

export function validateCredentialJson(rawText) {
  let data;
  try {
    data = typeof rawText === 'string' ? JSON.parse(rawText) : rawText;
  } catch {
    throw new Error('凭据 JSON 格式无效');
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    throw new Error('凭据内容必须是 JSON 对象');
  }
  const client_id = String(data.client_id || '').trim();
  const refresh_token = String(data.refresh_token || '').trim();
  if (!client_id || !refresh_token) {
    throw new Error('凭据 JSON 必须包含 client_id 与 refresh_token');
  }
  return data;
}
