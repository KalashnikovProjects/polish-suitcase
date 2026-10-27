'use client';

import type { PatchOperation, TravelResponse, DynamicField } from '@/types/travel';
import { pointer } from '@/lib/api';
import { dateInput, parseDateInput } from '@/lib/presentation';
import { useState } from 'react';
import PeoplesCounter from './PeoplesCounter';
import LocationTree from './LocationTree';

const inputClass = 'mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100';
const box = 'space-y-3 rounded-2xl border border-slate-800 bg-slate-900 p-4';

export default function TripEditor({ data, busy, onPatch }: {
  data: TravelResponse; busy: boolean; onPatch: (operations: PatchOperation[]) => Promise<void>;
}) {
  const [dateError, setDateError] = useState('');
  function saveField(event: React.FormEvent<HTMLFormElement>, group: string, field: DynamicField) {
    event.preventDefault();
    const raw = new FormData(event.currentTarget).get('value') as string;
    const value = field.type === 'number' ? Number(raw) : field.type === 'tags' ? raw.split(',').map(x => x.trim()).filter(Boolean) : raw;
    void onPatch([{ op: 'replace', path: `/additional/${pointer(group)}/fields/${pointer(field.id)}/value`, value }]);
  }
  return <section className="flex min-h-[420px] w-full flex-col border-slate-800 bg-slate-950 lg:h-full lg:w-[38%] lg:border-r">
    <h2 className="border-b border-slate-800 p-5 font-semibold">Параметры поездки </h2>
    <div className="app-scrollbar min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
      <form key={`${data.id}:${data.revision}`} className={box} onSubmit={event => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        const start = parseDateInput(String(form.get('from')));
        const end = parseDateInput(String(form.get('to')));
        if (start === null || end === null) { setDateError('Введите существующую дату в формате ДД.ММ.ГГГГ'); return; }
        setDateError('');
        const operations: PatchOperation[] = [];
        const values = [
          ['/basic/departure', String(form.get('departure')), data.basic.departure],
          ['/basic/dates/from', start, data.basic.dates.from],
          ['/basic/dates/to', end, data.basic.dates.to],
          ['/basic/price_restrictions/min', (String(form.get('min')).trim() ? Number(form.get('min')) : -1), data.basic.price_restrictions.min],
          ['/basic/price_restrictions/max', (String(form.get('max')).trim() ? Number(form.get('max')) : -1), data.basic.price_restrictions.max],
        ] as const;
        for (const [path, value, old] of values) if (value !== old) operations.push({ op: 'replace', path, value });
        if (operations.some(op => op.path.includes('price_restrictions')) && data.additional.some(g => g.id === 'travel_details' && g.fields.some(f => f.id === 'budget_per_person'))) {
          operations.push({ op: 'replace', path: '/additional/travel_details/fields/budget_per_person/value', value: false });
          operations.push({ op: 'replace', path: '/additional/travel_details/fields/budget_unlimited/value', value: false });
        }
        if (operations.length) void onPatch(operations);
      }}>
        <label className="block text-sm">Точка отправления<input name="departure" defaultValue={data.basic.departure} required className={inputClass} /></label>
        <label className="block text-sm">Начало поездки<input name="from" defaultValue={dateInput(data.basic.dates.from)} placeholder="ДД.ММ.ГГГГ" inputMode="numeric" className={inputClass} /></label>
        <label className="block text-sm">Конец поездки<input name="to" defaultValue={dateInput(data.basic.dates.to)} placeholder="ДД.ММ.ГГГГ" inputMode="numeric" className={inputClass} /></label>
        <label className="block text-sm">Бюджет от, ₽<input name="min" type="number" min={0} defaultValue={data.basic.price_restrictions.min < 0 ? '' : data.basic.price_restrictions.min} placeholder="Не задан" className={inputClass} /></label>
        <label className="block text-sm">Общий бюджет до, ₽<input name="max" type="number" min={0} defaultValue={data.basic.price_restrictions.max < 0 ? '' : data.basic.price_restrictions.max} placeholder="Не задан" className={inputClass} /></label>
        <p className="text-xs text-slate-400">Бюджет можно оставить пустым. {data.basic.cost_range !== '-' && `Оценка поездки: ${data.basic.cost_range}`}</p>
        <button disabled={busy} className="rounded-lg bg-blue-600 px-4 py-2 text-sm disabled:opacity-40">Сохранить параметры</button>
      </form>
      {dateError && <p role="alert" className="text-sm text-rose-400">{dateError}</p>}
      <fieldset disabled={busy}>
        <PeoplesCounter peoples={data.basic.peoples}
          onUpdateCount={(type, value) => { void onPatch([
            { op: 'replace', path: `/basic/peoples/${type}`, value },
            { op: 'replace', path: '/basic/peoples/unknown', value: 0 },
          ]); }}
          onRemoveTag={tag => { void onPatch([{ op: 'replace', path: '/basic/peoples/additional-info', value: data.basic.peoples['additional-info'].filter(x => x !== tag) }]); }} />
      </fieldset>
      <fieldset disabled={busy} className={box}><legend className="px-2 text-sm">Рассматриваемые локации</legend>
        <LocationTree tree={data.basic.locations_tree} onRemovePath={path => { void onPatch([{ op: 'remove', path: '/basic/locations_tree/' + path }]); }} />
      </fieldset>
      {data.additional.map(group => <fieldset key={group.id} disabled={busy} className={box}>
        <legend className="px-2 text-sm font-medium">{group.title}</legend>
        {group.fields.map(field => {
          const path = `/additional/${pointer(group.id)}/fields/${pointer(field.id)}/value`;
          if (field.type === 'boolean') return <label key={field.id} className="flex gap-3 text-sm"><input type="checkbox" checked={Boolean(field.value)} onChange={event => { void onPatch([{ op: 'replace', path, value: event.target.checked }]); }} />{field.label}</label>;
          return <form key={`${field.id}:${JSON.stringify(field.value)}`} onSubmit={event => saveField(event, group.id, field)} className="space-y-2">
            <label className="block text-sm">{field.label}
              {field.id === 'autonomy' ? <select name="value" defaultValue={String(field.value)} className={inputClass}><option value="balanced">Предлагать варианты сразу</option><option value="autonomous">Подбирать самостоятельно</option><option value="guided">Уточнять перед поиском</option></select> :
                field.id === 'meal_plan' ? <select name="value" defaultValue={String(field.value)} className={inputClass}><option value="">Не задано</option><option value="none">Без питания</option><option value="breakfast">Завтрак</option><option value="half_board">Полупансион</option><option value="full_board">Полный пансион</option><option value="all_inclusive">Всё включено</option></select> : <input name="value" type={field.type === 'number' ? 'number' : 'text'} required={field.type === 'number'} min={field.id === 'rooms' ? 1 : field.type === 'number' ? 0 : undefined} max={field.id === 'rooms' ? 10 : undefined} defaultValue={Array.isArray(field.value) ? field.value.join(', ') : String(field.value)} className={inputClass} />}
            </label>
            <button className="text-xs text-blue-400">Сохранить {field.label.toLowerCase()}</button>
          </form>;
        })}
      </fieldset>)}
    </div>
  </section>;
}
