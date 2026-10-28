import type { DynamicAttribute, JsonValue, TravelResponse } from '@/types/travel';
import { formatDate, formatDateTime, userText, providerName } from '@/lib/presentation';

const display = (value: JsonValue): string => Array.isArray(value) ? value.map(display).join('; ') : value !== null && typeof value === 'object' ? Object.values(value).map(display).join(', ') : String(value ?? '');
const safeUrl = (value: string) => /^https?:\/\//i.test(value);
function Attributes({ items = [] }: { items?: DynamicAttribute[] }) {
  const visible = items.filter(item => !['Источник', 'Готовность', 'Допущения'].includes(item.label) && display(item.value) && display(item.value) !== 'Нет');
  if (!visible.length) return null;
  return <dl className="space-y-1 text-xs text-slate-400">{visible.map((item, index) => <div key={index}><dt className="inline font-medium">{item.label === 'Ограничения поиска' ? 'Условия' : item.label}: </dt><dd className="inline whitespace-pre-wrap">{item.label === 'Поставщик' ? providerName(display(item.value)) : userText(display(item.value))}</dd></div>)}</dl>;
}

export default function Variants({ data, onClose, onAdd, onOpenCart, cartCount, cartVariantIds, addingVariantId, addError }: {
  data: TravelResponse;
  onClose: () => void;
  onAdd: (variantId: number) => void;
  onOpenCart: () => void;
  cartCount: number;
  cartVariantIds: Set<number>;
  addingVariantId: number | null;
  addError: { variantId: number; message: string } | null;
}) {
  return <section id="trip-variants-panel" className="panel-surface flex h-full min-h-0 w-full flex-col">
    <header className="chat-panel-header flex shrink-0 flex-wrap items-center justify-center gap-2 px-4 py-3">
      <h2 className="panel-heading-label min-w-0 flex-1 xl:max-w-[360px]">
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5 shrink-0"><circle cx="12" cy="12" r="9" /><path d="m15.5 8.5-2.2 4.8-4.8 2.2 2.2-4.8 4.8-2.2Z" /></svg>
        <span>Варианты поездки</span>
      </h2>
      <button type="button" onClick={onOpenCart} className="panel-toggle-button shrink-0" aria-label={`Открыть корзину, позиций: ${cartCount}`}>
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4"><circle cx="9" cy="20" r="1" /><circle cx="19" cy="20" r="1" /><path d="M2 3h2l2.4 11.5a2 2 0 0 0 2 1.5H20l2-9H5" /></svg>
        <span>Корзина · {cartCount}</span>
      </button>
      <button type="button" onClick={onClose} className="panel-close-button xl:hidden">Скрыть</button>
    </header>
    <div className="app-scrollbar min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
      {!data.variants.length && <p className="text-sm text-slate-400">Здесь появятся подходящие варианты. Пожелания можно менять в чате.</p>}
      {data.variants.map(variant => <article key={variant.id} className="space-y-3 rounded-2xl border border-slate-700 bg-slate-900 p-4">
        <h3 className="font-semibold">{variant.name}</h3>
        <p className="text-xs text-slate-400">{variant.location} · {formatDate(variant.dates.from)} — {formatDate(variant.dates.to)}</p>
        <p className="text-sm font-semibold text-emerald-400">{userText(variant.price)}</p>
        <p className="whitespace-pre-line text-sm leading-relaxed text-slate-200">{userText(variant.description)}</p>
        {variant.habitations.map(hotel => <div key={hotel.id} className="space-y-2 rounded-lg bg-slate-950 p-3">
          <h4 className="text-sm">🏨 {hotel.name}</h4><p className="text-xs">{hotel.price} · {formatDate(hotel.dates.from)} — {formatDate(hotel.dates.to)}</p>
          <p className="text-xs text-slate-400">{userText(hotel.description)}</p><Attributes items={hotel.additional} />
          {safeUrl(hotel.url) && <a href={hotel.url} target="_blank" rel="noopener noreferrer" className="text-xs text-blue-400">Подробнее об отеле</a>}
        </div>)}
        {variant.transports.map(transport => <div key={transport.id} className="space-y-2 rounded-lg bg-slate-950 p-3 text-xs">
          <h4>{transport.type === 'flight' ? '✈' : transport.type === 'bus' ? '🚌' : '🚆'} {transport.name}</h4><p>{transport.price}</p>
          <p>{formatDateTime(transport.times?.from)} → {formatDateTime(transport.times?.to)}</p><Attributes items={transport.additional} />
        </div>)}
        <details className="text-xs text-slate-400"><summary className="cursor-pointer">Условия и важные детали</summary><div className="mt-2"><Attributes items={variant.additional} /></div></details>
        {variant.urls.filter(safeUrl).map(url => <a key={url} href={url} target="_blank" rel="noopener noreferrer" className="block text-xs text-blue-400">{url.includes('rasp.yandex.ru') ? 'Посмотреть на Яндекс Расписаниях' : 'Посмотреть предложение'}</a>)}
        <div className="sticky bottom-2 z-10 rounded-xl bg-slate-900/95">
          <button type="button" onClick={() => cartVariantIds.has(variant.id) ? onOpenCart() : onAdd(variant.id)}
            disabled={addingVariantId !== null}
            className="w-full rounded-xl bg-[var(--color-info)] px-4 py-4 text-base font-bold text-white transition-colors hover:bg-[var(--color-info-hover)] disabled:cursor-not-allowed disabled:opacity-40">
            {addingVariantId === variant.id ? 'Добавляем…' : cartVariantIds.has(variant.id) ? 'Открыть в корзине →' : 'Добавить вариант в корзину →'}
          </button>
          {addError?.variantId === variant.id && <p role="alert" className="mt-2 text-center text-xs text-rose-300">{addError.message}</p>}
        </div>
      </article>)}
    </div>
  </section>;
}
