'use client';

import { useEffect, useRef } from 'react';
import type { Cart, DemoOrder } from '@/types/commerce';
import { formatDate, formatDateTime, userText } from '@/lib/presentation';

interface Props {
  cart: Cart | null;
  orders: DemoOrder[];
  error: string | null;
  busy: boolean;
  loading: boolean;
  latestOrder: DemoOrder | null;
  onClose: () => void;
  onRetry: () => void;
  onRemove: (itemId: string) => void;
  onRefresh: (itemId: string) => void;
  onSelect: (itemId: string, lineId: string, selected: boolean) => void;
  onCheckout: () => void;
}

export default function CartDrawer({ cart, orders, error, busy, loading, latestOrder,
  onClose, onRetry, onRemove, onRefresh, onSelect, onCheckout }: Props) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      if (event.key !== 'Tab' || !dialogRef.current) return;
      const controls = Array.from(dialogRef.current.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), summary, a[href]'));
      if (!controls.length) return;
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => { window.removeEventListener('keydown', onKeyDown); previousFocus?.focus(); };
  }, [onClose]);

  const selectedStale = cart?.items.some(item => item.stale && item.lines.some(line => line.selected)) ?? false;

  return <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/75" onMouseDown={event => {
    if (event.target === event.currentTarget) onClose();
  }}>
    <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="cart-title"
      className="flex h-full w-full max-w-xl flex-col border-l border-slate-700 bg-slate-900 text-slate-100 shadow-2xl">
      <header className="flex items-center justify-between border-b border-slate-700 px-5 py-4">
        <div>
          <h2 id="cart-title" className="text-xl font-semibold">Корзина поездки</h2>
        </div>
        <button ref={closeRef} type="button" onClick={onClose} aria-label="Закрыть корзину"
          className="rounded-lg border border-slate-600 px-3 py-2 text-sm hover:bg-slate-800">Закрыть</button>
      </header>
      <div className="app-scrollbar min-h-0 flex-1 space-y-5 overflow-y-auto p-5">
        {latestOrder && <div role="status" className="rounded-xl border border-emerald-700 bg-emerald-950/40 p-4">
          <p className="font-semibold text-emerald-300">Демо-оплата подтверждена</p>
          <p className="text-xs text-slate-300">Заказ {latestOrder.id}. Никаких списаний и бронирований не было.</p>
        </div>}
        {error && <div role="alert" className="rounded-xl border border-rose-800 bg-rose-950/40 p-3 text-sm text-rose-200">
          {error} <button type="button" onClick={onRetry} className="ml-2 underline">Обновить корзину</button>
        </div>}
        {loading && <p className="text-sm text-slate-400">Загружаем корзину…</p>}
        {!loading && cart && cart.items.length === 0 && <p className="text-sm text-slate-400">Корзина пуста. Добавьте готовый вариант поездки.</p>}
        {cart?.items.map(item => <article key={item.id} className="space-y-3 rounded-2xl border border-slate-700 bg-slate-950/60 p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="font-semibold">{userText(item.name)}</h3>
              <p className="text-xs text-slate-400">{userText(item.location)} · {formatDate(item.dates.from)} — {formatDate(item.dates.to)}</p>
            </div>
            <button type="button" disabled={busy} onClick={() => onRemove(item.id)} className="text-xs text-rose-300 underline disabled:opacity-50">Удалить</button>
          </div>
          {item.stale && <div className="rounded-lg border border-amber-700 bg-amber-950/40 p-3 text-xs text-amber-200">
            Вариант изменился или больше недоступен. Обновите позицию перед оплатой либо удалите её.
            <button type="button" disabled={busy} onClick={() => onRefresh(item.id)} className="ml-2 font-semibold underline disabled:opacity-50">Обновить</button>
          </div>}
          <ul className="space-y-2">{item.lines.map(line => <li key={line.id} className="rounded-lg border border-slate-800 bg-slate-900 p-3">
            <label className="flex cursor-pointer items-start gap-3">
              <input type="checkbox" checked={line.selected} disabled={busy} onChange={event => onSelect(item.id, line.id, event.target.checked)} className="mt-1" />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium">{line.kind === 'hotel' ? '🏨 Отель' : '🎫 Транспорт'}: {userText(line.name)}</span>
                <span className="block text-xs text-slate-400">{line.kind === 'hotel'
                  ? `${formatDate(line.dates?.from ?? '-')} — ${formatDate(line.dates?.to ?? '-')}`
                  : `${formatDateTime(line.times?.from)}${line.times?.to ? ` → ${formatDateTime(line.times.to)}` : ''}`}</span>
              </span>
              <span className="text-right text-sm text-emerald-300">{userText(line.price)}</span>
            </label>
          </li>)}</ul>
        </article>)}
        {orders.length > 0 && <section className="space-y-2 border-t border-slate-700 pt-4">
          <h3 className="font-semibold">История демо-оплат</h3>
          {orders.map(order => <details key={order.id} className="rounded-lg border border-slate-700 p-3 text-sm">
            <summary className="cursor-pointer">{new Date(order.paid_at).toLocaleString('ru-RU')} · {order.items.reduce((n, item) => n + item.lines.length, 0)} поз.</summary>
            <p className="mt-2 text-xs text-slate-400">Заказ {order.id}. Деньги не списаны.</p>
            {order.items.map(item => <div key={`${item.chat_id}:${item.variant_id}`} className="mt-2">
              <p className="font-medium">{userText(item.name)}</p>
              {item.lines.map(line => <p key={line.id} className="text-xs text-slate-300">{userText(line.name)} — {userText(line.price)}</p>)}
            </div>)}
          </details>)}
        </section>}
      </div>
      <footer className="space-y-2 border-t border-slate-700 p-5">
        <p className="text-xs text-slate-400">Отмечено позиций: {cart?.selected_count ?? 0}. Цены показаны как в предложении; точную сумму и доступность нужно проверять у поставщика.</p>
        <button type="button" onClick={onCheckout} disabled={busy || loading || !cart?.selected_count || selectedStale}
          className="w-full rounded-xl bg-[var(--color-primary)] px-5 py-4 text-base font-bold text-white shadow-lg transition-colors hover:bg-[var(--color-primary-hover)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-focus)] disabled:cursor-not-allowed disabled:opacity-40">
          {busy ? 'Обрабатываем…' : 'Подтвердить оплату'}
        </button>
      </footer>
    </section>
  </div>;
}
