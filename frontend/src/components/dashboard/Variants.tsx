import type { DynamicAttribute, JsonValue, TravelResponse } from '@/types/travel';

const display = (value: JsonValue): string => Array.isArray(value) ? value.map(display).join('; ') : value !== null && typeof value === 'object' ? JSON.stringify(value) : String(value ?? '—');
const safeUrl = (value: string) => /^https?:\/\//i.test(value);
function Attributes({ items = [] }: { items?: DynamicAttribute[] }) {
  return <dl className="space-y-1 text-xs text-slate-400">{items.filter(item => display(item.value)).map((item, index) => <div key={index}><dt className="inline font-medium">{item.label}: </dt><dd className="inline whitespace-pre-wrap">{display(item.value)}</dd></div>)}</dl>;
}

export default function Variants({ data }: { data: TravelResponse }) {
  return <section className="flex min-h-[350px] w-full flex-col bg-slate-900/60 lg:h-full lg:w-[30%]">
    <h2 className="border-b border-slate-800 p-5 font-semibold">Варианты поездки</h2>
    <p role="status" className="px-5 py-3 text-sm text-amber-300">{data.agent['status-message'] || ({ idle: 'Ожидаем агента', working: 'Идёт подбор', waiting_user: 'Нужно уточнение', completed: 'Подбор завершён', error: 'Ошибка обработки' }[data.agent.status])}</p>
    <div className="app-scrollbar min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
      {!data.variants.length && <p className="text-sm text-slate-400">Варианты появятся после уточнения требований. История диалога сохранена.</p>}
      {data.variants.map(variant => <article key={variant.id} className="space-y-3 rounded-2xl border border-slate-700 bg-slate-900 p-4">
        <h3 className="font-semibold">{variant.name}</h3>
        <p className="text-xs text-slate-400">{variant.location} · {variant.dates.from} — {variant.dates.to}</p>
        <p className="text-sm font-semibold text-emerald-400">{variant.price}</p>
        <p className="text-xs">{variant.description}</p>
        {!variant.completed && <p className="text-xs text-amber-400">Сборка ещё не завершена</p>}
        {variant.habitations.map(hotel => <div key={hotel.id} className="space-y-2 rounded-lg bg-slate-950 p-3">
          <h4 className="text-sm">🏨 {hotel.name}</h4><p className="text-xs">{hotel.price} · {hotel.dates.from} — {hotel.dates.to}</p>
          <p className="text-xs text-slate-400">{hotel.description}</p><Attributes items={hotel.additional} />
          {safeUrl(hotel.url) && <a href={hotel.url} target="_blank" rel="noopener noreferrer" className="text-xs text-blue-400">Источник отеля</a>}
        </div>)}
        {variant.transports.map(transport => <div key={transport.id} className="space-y-2 rounded-lg bg-slate-950 p-3 text-xs">
          <h4>{transport.type === 'flight' ? '✈' : '🚆'} {transport.name}</h4><p>{transport.price}</p>
          <p>{transport.times?.from} → {transport.times?.to}</p><Attributes items={transport.additional} />
        </div>)}
        <Attributes items={variant.additional} />
        {variant.urls.filter(safeUrl).map(url => <a key={url} href={url} target="_blank" rel="noopener noreferrer" className="block break-all text-xs text-blue-400">{url}</a>)}
        <p className="text-[11px] text-slate-500">Предложение для планирования. Цена и доступность требуют проверки перед оформлением.</p>
      </article>)}
    </div>
  </section>;
}
