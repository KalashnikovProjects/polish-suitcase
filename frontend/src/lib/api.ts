let refreshing: Promise<string | null> | null = null;
let sessionGeneration = 0;
export function invalidateSession() { sessionGeneration += 1; refreshing = null; }

export function refreshSession(): Promise<string | null> {
  if (!refreshing) {
    const generation = sessionGeneration;
    const pending = fetch('/api/v1/auth/refresh', { method: 'POST', credentials: 'same-origin' })
      .then(async response => {
        const value = response.ok ? (await response.json()).access_token as string : null;
        return generation === sessionGeneration ? value : null;
      })
      .catch(() => null)
      .finally(() => { if (refreshing === pending) refreshing = null; });
    refreshing = pending;
  }
  return refreshing;
}

export async function apiRequest(path: string, token: string, init: RequestInit = {}, onToken?: (value: string | null) => void) {
  const generation = sessionGeneration;
  const send = (access: string) => fetch(path, { ...init, headers: { ...init.headers, Authorization: `Bearer ${access}` } });
  let response = await send(token);
  if (generation !== sessionGeneration) throw new Error('Сессия изменилась');
  if (response.status === 401) {
    const renewed = await refreshSession();
    if (generation !== sessionGeneration) throw new Error('Сессия изменилась');
    onToken?.(renewed);
    if (renewed) response = await send(renewed);
  }
  if (generation !== sessionGeneration) throw new Error('Сессия изменилась');
  return response;
}

export function pointer(value: string) { return value.replaceAll('~', '~0').replaceAll('/', '~1'); }
