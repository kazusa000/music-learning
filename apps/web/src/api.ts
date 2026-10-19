import type {Operation} from '../../../packages/contracts/types';

export async function result<T>(response: Response): Promise<T> {
  const data = await response.json();
  if (!response.ok || !data.ok) {
    const details=Array.isArray(data.error?.details)?data.error.details.slice(0,3).map((item:{msg?:unknown}|null)=>typeof item?.msg==='string'?item.msg.slice(0,240):'').filter(Boolean).join('；'):'';
    throw Object.assign(new Error((data.error?.message ?? data.error?.code ?? '连接失败')+(details?'：'+details:'')),{code:data.error?.code,status:response.status});
  }
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
    history.replaceState(null, '', location.pathname + location.search);
    const response = await fetch('/v1/session', {method: 'POST', headers: {Authorization: `Bearer ${token}`}});
    if (!response.ok) throw new Error('本机会话失效，请通过启动器重新打开。');
  }
  // The UI uses generated contracts. Fetching every operation schema is for
  // tool discovery, not a prerequisite for displaying the user's project.
  const response=await fetch('/health');
  if(!response.ok)throw new Error('本机服务尚未就绪。');
  return response.json();
}
