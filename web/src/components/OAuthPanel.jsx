import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  ShieldCheck, KeyRound, ExternalLink, Copy, RefreshCw, Unplug,
  Upload, CheckCircle2,
} from 'lucide-react';
import { api, post, remove, enc } from '../api';
import { Button, Field, ErrorBox, Status } from '../ui';
import { parseGoogleClientJson, validateCredentialJson } from '../oauthHelpers';

const PURPOSE_META = {
  colab: {
    title: 'Google Colab（算力账号）',
    subtitle: '用于按需申请与接管 GPU 运行时、查询可用机型与 CU 额度',
  },
  drive: {
    title: 'Google Drive（存储账号）',
    subtitle: '用于扫描同步模型权重，并自动归档作品原图与参数旁车（可与 Colab 不同账号）',
  },
};

export default function OAuthPanel({ refresh, setNotice }) {
  const [statuses, setStatuses] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [activePurpose, setActivePurpose] = useState(null);

  const loadStatus = useCallback(async () => {
    try {
      const data = await api('/api/oauth/status');
      setStatuses(data);
      setLoadError('');
    } catch (e) {
      setLoadError(e.message);
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  return (
    <section className="stack">
      <div className="row between wrap" style={{ gap: '8px' }}>
        <div>
          <h3>账号授权（Google Colab / Google Drive）</h3>
          <p className="muted small">
            支持零公网回调 PKCE 授权、同源 HTTPS 弹窗回调，或直接导入已有凭据 JSON。凭据仅以 0600 权限保存在服务端本地。
          </p>
        </div>
        <Button className="mini-btn" onClick={loadStatus}>
          <RefreshCw size={13} />
          刷新状态
        </Button>
      </div>

      <ErrorBox error={loadError} retry={loadStatus} />

      <div className="oauth-grid">
        {['colab', 'drive'].map(purpose => (
          <OAuthAccountCard
            key={purpose}
            purpose={purpose}
            meta={PURPOSE_META[purpose]}
            info={statuses?.[purpose] || null}
            expanded={activePurpose === purpose}
            onToggle={() => setActivePurpose(cur => (cur === purpose ? null : purpose))}
            onReload={async () => {
              await loadStatus();
              await refresh(['settings', 'runtime']);
            }}
            setNotice={setNotice}
          />
        ))}
      </div>
    </section>
  );
}

function OAuthAccountCard({ purpose, meta, info, expanded, onToggle, onReload, setNotice }) {
  const [mode, setMode] = useState('manual');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [redirectUri, setRedirectUri] = useState('');
  const [flow, setFlow] = useState(null);
  const [codeOrUrl, setCodeOrUrl] = useState('');
  const [credJson, setCredJson] = useState('');
  const [verifyImport, setVerifyImport] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [probeMsg, setProbeMsg] = useState('');

  const clientFileRef = useRef(null);
  const credFileRef = useRef(null);

  useEffect(() => {
    if (!redirectUri && info?.default_manual_redirect_uri) {
      setRedirectUri(info.default_manual_redirect_uri);
    }
  }, [info?.default_manual_redirect_uri, redirectUri]);

  useEffect(() => {
    const onMsg = async event => {
      if (typeof window !== 'undefined' && event.origin !== window.location.origin) return;
      const msg = event.data;
      if (!msg || msg.type !== 'studio:oauth-complete' || msg.purpose !== purpose) return;
      if (msg.ok) {
        setError('');
        setFlow(null);
        setProbeMsg(`${meta.title} 已完成授权${msg.account_email ? `（${msg.account_email}）` : ''}`);
        setNotice?.(`${purpose.toUpperCase()} 授权已保存`);
        await onReload();
        if (expanded) onToggle();
      } else {
        setError(msg.error || 'Google 回调授权未完成');
      }
    };
    window.addEventListener('message', onMsg);
    return () => window.removeEventListener('message', onMsg);
  }, [purpose, meta.title, expanded, onToggle, onReload, setNotice]);

  const copyText = async (text, msg) => {
    try {
      await navigator.clipboard.writeText(text);
      setNotice?.(msg);
    } catch {
      setError('剪贴板写入失败，请手动选中复制');
    }
  };

  const handleClientFile = async e => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    try {
      const parsed = parseGoogleClientJson(await file.text());
      setClientId(parsed.client_id);
      if (parsed.client_secret) setClientSecret(parsed.client_secret);
      if (mode === 'manual' && parsed.redirect_uri) {
        setRedirectUri(parsed.redirect_uri);
      }
      setError('');
      setNotice?.(`已读取客户端配置（${parsed.kind === 'installed' ? '桌面应用' : parsed.kind === 'web' ? 'Web 应用' : 'JSON'}）`);
    } catch (err) {
      setError(err.message);
    }
  };

  const handleCredFile = async e => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    try {
      setCredJson(await file.text());
      setError('');
    } catch (err) {
      setError(err.message);
    }
  };

  const startManual = async () => {
    setBusy('start');
    setError('');
    try {
      const res = await post(`/api/oauth/${enc(purpose)}/start`, {
        flow_mode: 'manual',
        ...(clientId.trim() ? { client_id: clientId.trim() } : {}),
        ...(clientSecret.trim() ? { client_secret: clientSecret.trim() } : {}),
        ...(redirectUri.trim() ? { redirect_uri: redirectUri.trim() } : {}),
      });
      setFlow(res);
      setCodeOrUrl('');
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy('');
    }
  };

  const exchangeManual = async () => {
    if (!codeOrUrl.trim()) {
      setError('请粘贴跳转后的完整 URL 或授权码 code');
      return;
    }
    setBusy('exchange');
    setError('');
    try {
      const res = await post(`/api/oauth/${enc(purpose)}/exchange`, {
        flow_id: flow?.flow_id,
        code_or_url: codeOrUrl.trim(),
      });
      setFlow(null);
      setCodeOrUrl('');
      setProbeMsg(`授权成功${res.account_email ? `（${res.account_email}）` : ''}`);
      setNotice?.(`${purpose.toUpperCase()} 授权已保存`);
      await onReload();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy('');
    }
  };

  const startWebPopup = async () => {
    setBusy('popup');
    setError('');
    let popup = null;
    try {
      popup = window.open('about:blank', `studio-oauth-${purpose}`, 'width=540,height=720');
    } catch {}
    try {
      const res = await post(`/api/oauth/${enc(purpose)}/start`, {
        flow_mode: 'web_callback',
        ...(clientId.trim() ? { client_id: clientId.trim() } : {}),
        ...(clientSecret.trim() ? { client_secret: clientSecret.trim() } : {}),
      });
      setFlow(res);
      if (popup && !popup.closed) {
        popup.location.href = res.auth_url;
      }
    } catch (e) {
      if (popup && !popup.closed) popup.close();
      setError(e.message);
    } finally {
      setBusy('');
    }
  };

  const importCred = async () => {
    setBusy('import');
    setError('');
    try {
      const parsed = validateCredentialJson(credJson);
      const res = await post(`/api/oauth/${enc(purpose)}/import`, {
        credential_json: parsed,
        verify: verifyImport,
      });
      setCredJson('');
      setProbeMsg(`凭据已导入${res.account_email ? `（${res.account_email}）` : ''}`);
      setNotice?.(`${purpose.toUpperCase()} 凭据已导入并保存`);
      await onReload();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy('');
    }
  };

  const testConn = async () => {
    setBusy('test');
    setError('');
    setProbeMsg('');
    try {
      const res = await post(`/api/oauth/${enc(purpose)}/test`);
      setProbeMsg(res.message || '连通性测试通过');
      await onReload();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy('');
    }
  };

  const disconnect = async () => {
    if (!confirm(`确定解绑 ${meta.title}？这将删除服务端保存的 ${info?.path_hint || purpose + '.json'} 凭据文件。`)) return;
    setBusy('disconnect');
    setError('');
    setProbeMsg('');
    try {
      await remove(`/api/oauth/${enc(purpose)}`);
      setFlow(null);
      setNotice?.(`已解绑 ${purpose.toUpperCase()}`);
      await onReload();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy('');
    }
  };

  const configured = !!info?.configured;
  const webCallbackUrl = info?.callback_url?.startsWith('http')
    ? info.callback_url
    : `${typeof window !== 'undefined' ? window.location.origin : ''}/api/oauth/${purpose}/callback`;

  return (
    <article className="oauth-card">
      <div className="row between wrap" style={{ gap: '8px' }}>
        <div className="grow" style={{ minWidth: 0 }}>
          <strong>{meta.title}</strong>
          <p className="muted small">{meta.subtitle}</p>
        </div>
        <Status state={configured ? 'ready' : 'off'} label={configured ? '已授权' : '未授权'} />
      </div>

      <div className="oauth-meta">
        {info?.account_email && <span>账号：{info.account_email}</span>}
        <span>客户端：{info?.client_id_preview || '未配置'}</span>
        <span>文件：{info?.path_hint || `${purpose}.json`}</span>
      </div>

      {probeMsg && (
        <div className="oauth-ok-banner" role="status">
          <CheckCircle2 size={15} />
          <span>{probeMsg}</span>
        </div>
      )}
      <ErrorBox error={error} />

      <div className="row wrap" style={{ gap: '8px' }}>
        <Button primary={!configured} onClick={onToggle} disabled={!!busy}>
          <KeyRound size={15} />
          {expanded ? '收起向导' : configured ? '重新授权 / 换号' : '手动授权绑定'}
        </Button>
        <Button onClick={testConn} disabled={!configured || !!busy}>
          <ShieldCheck size={15} />
          {busy === 'test' ? '测试中…' : '测试连通性'}
        </Button>
        {configured && (
          <Button onClick={disconnect} disabled={!!busy}>
            <Unplug size={15} />
            解绑
          </Button>
        )}
      </div>

      {expanded && (
        <div className="oauth-wizard stack">
          <div className="oauth-tabs" role="tablist">
            {[
              ['manual', '手动复制授权码 (PKCE)'],
              ['web_callback', '同源弹窗回调 (Web)'],
              ['import', '导入凭据 JSON'],
            ].map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={mode === id}
                className={mode === id ? 'selected' : ''}
                onClick={() => {
                  setMode(id);
                  setError('');
                  setFlow(null);
                }}
              >
                {label}
              </button>
            ))}
          </div>

          {mode !== 'import' ? (
            <div className="stack">
              <div className="row between wrap" style={{ gap: '8px' }}>
                <small className="muted">
                  可在 Google Cloud Console 创建 OAuth 客户端后直接上传 `client_secret_*.json` 自动填入。
                </small>
                <input ref={clientFileRef} type="file" accept=".json,application/json" hidden onChange={handleClientFile} />
                <Button className="mini-btn" onClick={() => clientFileRef.current?.click()}>
                  <Upload size={13} />
                  读取 client_secret.json
                </Button>
              </div>

              <div className="grid-two">
                <Field
                  label="Google OAuth Client ID"
                  hint={info?.has_client_id && !clientId ? `留空则复用已配置客户端（${info.client_id_preview}）` : undefined}
                >
                  <input
                    value={clientId}
                    onChange={e => setClientId(e.target.value)}
                    placeholder={info?.has_client_id ? `已配置（${info.client_id_preview}）` : '123456-abc.apps.googleusercontent.com'}
                  />
                </Field>
                <Field
                  label="Client Secret（可选）"
                  hint="桌面端或 Web 客户端需填写；无密钥 PKCE 可留空"
                >
                  <input
                    type="password"
                    autoComplete="off"
                    value={clientSecret}
                    onChange={e => setClientSecret(e.target.value)}
                    placeholder={info?.has_client_secret ? '已保存（留空保留原密钥）' : 'GOCSPX-...（可留空）'}
                  />
                </Field>
              </div>

              {mode === 'manual' ? (
                <div className="stack">
                  <Field
                    label="重定向 Redirect URI（须在 GCP 客户端白名单中）"
                    hint="Google 跳转后即使浏览器显示 127.0.0.1 无法打开也正常，直接复制地址栏完整 URL 粘到第 2 步即可。"
                  >
                    <input
                      value={redirectUri}
                      onChange={e => setRedirectUri(e.target.value)}
                      placeholder="http://127.0.0.1:8765/callback"
                    />
                  </Field>
                  <div className="chips">
                    <button
                      type="button"
                      className={redirectUri === (info?.default_manual_redirect_uri || 'http://127.0.0.1:8765/callback') ? 'selected' : ''}
                      onClick={() => setRedirectUri(info?.default_manual_redirect_uri || 'http://127.0.0.1:8765/callback')}
                    >
                      本地回环 127.0.0.1:8765
                    </button>
                    <button
                      type="button"
                      className={redirectUri === info?.adc_redirect_uri ? 'selected' : ''}
                      onClick={() => setRedirectUri(info?.adc_redirect_uri || 'https://sdk.cloud.google.com/applicationdefaultauthcode.html')}
                    >
                      Google Cloud SDK 回调页
                    </button>
                  </div>

                  <div className="row wrap" style={{ gap: '8px' }}>
                    <Button primary onClick={startManual} disabled={!!busy}>
                      <KeyRound size={15} />
                      {busy === 'start' ? '正在生成…' : '1. 生成 PKCE 授权链接'}
                    </Button>
                  </div>

                  {flow?.flow_mode === 'manual' && (
                    <div className="oauth-step-box stack">
                      <div className="row between wrap" style={{ gap: '8px' }}>
                        <strong>第 1 步：前往 Google 完成账号授权</strong>
                        <div className="row" style={{ gap: '6px' }}>
                          <a className="button mini-btn" href={flow.auth_url} target="_blank" rel="noreferrer">
                            打开授权页
                            <ExternalLink size={13} />
                          </a>
                          <Button className="mini-btn" onClick={() => copyText(flow.auth_url, '授权链接已复制')}>
                            <Copy size={13} />
                            复制链接
                          </Button>
                        </div>
                      </div>
                      <code className="oauth-url-box">{flow.auth_url}</code>

                      <Field label="第 2 步：粘贴跳转后的完整 URL 或授权码 code">
                        <input
                          value={codeOrUrl}
                          onChange={e => setCodeOrUrl(e.target.value)}
                          placeholder="http://127.0.0.1:8765/callback?state=...&code=4/0A... 或直接粘贴 4/0A..."
                        />
                      </Field>
                      <Button primary onClick={exchangeManual} disabled={!!busy || !codeOrUrl.trim()}>
                        <ShieldCheck size={15} />
                        {busy === 'exchange' ? '正在换取令牌…' : '2. 交换令牌并保存'}
                      </Button>
                    </div>
                  )}
                </div>
              ) : (
                <div className="stack">
                  <div className="oauth-step-box stack">
                    <div className="row between wrap" style={{ gap: '8px' }}>
                      <strong className="small">请先将以下回调地址加入 GCP Web 客户端的重定向 URI 列表：</strong>
                      <Button className="mini-btn" onClick={() => copyText(webCallbackUrl, '回调地址已复制')}>
                        <Copy size={13} />
                        复制回调地址
                      </Button>
                    </div>
                    <code className="oauth-url-box">{webCallbackUrl}</code>
                  </div>
                  <div className="row wrap" style={{ gap: '8px' }}>
                    <Button primary onClick={startWebPopup} disabled={!!busy}>
                      <ExternalLink size={15} />
                      {busy === 'popup' ? '正在发起授权…' : '弹窗打开 Google 授权'}
                    </Button>
                    {flow?.flow_mode === 'web_callback' && (
                      <a className="button" href={flow.auth_url} target="_blank" rel="noreferrer">
                        弹窗被拦截？点此打开授权页
                        <ExternalLink size={14} />
                      </a>
                    )}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="stack">
              <div className="row between wrap" style={{ gap: '8px' }}>
                <small className="muted">适合从本机 CLI 或已有配置直接导入（需含 client_id 与 refresh_token）</small>
                <input ref={credFileRef} type="file" accept=".json,application/json" hidden onChange={handleCredFile} />
                <Button className="mini-btn" onClick={() => credFileRef.current?.click()}>
                  <Upload size={13} />
                  选择 {purpose}.json 文件
                </Button>
              </div>
              <Field label="凭据 JSON 内容">
                <textarea
                  className="code tall"
                  value={credJson}
                  onChange={e => setCredJson(e.target.value)}
                  placeholder='{"client_id": "...", "client_secret": "...", "refresh_token": "1//0..."}'
                />
              </Field>
              <label className="row between" style={{ gap: '10px', cursor: 'pointer' }}>
                <span>保存前向 Google 实时校验 refresh_token 有效性（推荐）</span>
                <input
                  type="checkbox"
                  checked={verifyImport}
                  onChange={e => setVerifyImport(e.target.checked)}
                  style={{ width: '18px', height: '18px' }}
                />
              </label>
              <Button primary onClick={importCred} disabled={!!busy || !credJson.trim()}>
                <ShieldCheck size={15} />
                {busy === 'import' ? '正在校验保存…' : '验证并导入凭据'}
              </Button>
            </div>
          )}
        </div>
      )}
    </article>
  );
}
