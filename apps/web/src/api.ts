import type {Operation} from '../../../packages/contracts/types';

export async function result<T>(response: Response): Promise<T> {
  const data = await response.json();
  if (!response.ok || !data.ok) throw new Error(data.error?.message ?? data.error?.code ?? '连接失败');
  return data.result;
}

export async function call<T>(operation: Operation, args: object = {}, expectedRevision?: string, write = false): Promise<T> {
  return result<T>(await fetch('/v1/commands', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({schema_version: 1, operation, arguments: args, expected_revision: expectedRevision ?? null,
      idempotency_key: write ? crypto.randomUUID().replaceAll('-', '') : null})}));
}

export async function connect() {
  const token = new URLSearchParams(location.hash.slice(1)).get('token');
  if (token) {
    history.replaceState(null, '', location.pathname);
    const response = await fetch('/v1/session', {method: 'POST', headers: {Authorization: `Bearer ${token}`}});
    if (!response.ok) throw new Error('本机会话失效，请通过启动器重新打开。');
  }
  return call('describe_capabilities');
}
