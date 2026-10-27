import type { DynamicAttribute, JsonValue, TravelResponse } from '@/types/travel';
import { formatDate, formatDateTime, userText, providerName } from '@/lib/presentation';

const display = (value: JsonValue): string => Array.isArray(value) ? value.map(display).join('; ') : value !== null && typeof value === 'object' ? Object.values(value).map(display).join(', ') : String(value ?? '');
const safeUrl = (value: string) => /^https?:\/\//i.test(value);
function Attributes({ items = [] }: { items?: DynamicAttribute[] }) {
  const visible = items.filter(item => !['Источник', 'Готовность', 'Допущения'].includes(item.label) && display(item.value) && display(item.value) !== 'Нет');
  if (!visible.length) return null;
  return <dl className="space-y-1 text-xs text-slate-400">{visible.map((item, index) => <div key={index}><dt className="inline font-medium">{item.label === 'Ограничения поиска' ? 'Условия' : item.label}: </dt><dd className="inline whitespace-pre-wrap">{item.label === 'Поставщик' ? providerName(display(item.value)) : userText(display(item.value))}</dd></div>)}</dl>;
}

export default function Variants({ data }: { data: TravelResponse }) {
  return <section className="flex min-h-[350px] w-full flex-col bg-slate-900/60 lg:h-full lg:w-[30%]">
    <h2 className="border-b border-slate-800 p-5 font-semibold">Варианты поездки</h2>
    <p role="status" className="px-5 py-3 text-sm text-amber-300">{userText(data.agent['status-message']) || ({ idle: 'Начинаем подбор', working: 'Подбираю варианты', waiting_user: 'Нужно одно уточнение', completed: 'Подбор завершён', error: 'Не удалось обновить подбор' }[data.agent.status])}</p>
    <div className="app-scrollbar min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
      {!data.variants.length && <p className="text-sm text-slate-400">Здесь появятся подходящие варианты. Пожелания можно менять в чате.</p>}
      {data.variants.map(variant => <article key={variant.id} className="space-y-3 rounded-2xl border border-slate-700 bg-slate-900 p-4">
        <h3 className="font-semibold">{variant.name}</h3>
        <p className="text-xs text-slate-400">{variant.location} · {formatDate(variant.dates.from)} — {formatDate(variant.dates.to)}</p>
        <p className="text-sm font-semibold text-emerald-400">{userText(variant.price)}</p>
        {variant.habitations.some(h => h.additional?.some(item => item.label === 'Источник' && ['sandbox', 'synthetic'].includes(String(item.value)))) && <p className="rounded bg-amber-950/60 px-2 py-1 text-xs text-amber-300">Цена жилья демонстрационная; актуальную стоимость нужно проверить.</p>}
        <p className="text-xs">{userText(variant.description)}</p>
        {!variant.completed && <p className="text-xs text-amber-400">Уточняю этот вариант</p>}
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
      </article>)}
    </div>
  </section>;
}
