'use client';

import type { PatchOperation, TravelResponse, DynamicField } from '@/types/travel';
import { pointer } from '@/lib/api';
import { dateInput, parseDateInput } from '@/lib/presentation';
import { useEffect, useState } from 'react';
import PeoplesCounter from './PeoplesCounter';
import LocationTree from './LocationTree';
import { groupCardClass, groupHeadingClass } from './groupStyles';

const inputClass = 'mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100';

export default function TripEditor({ data, busy, error, onPatch, onClose }: {
  data: TravelResponse; busy: boolean; error: string | null; onPatch: (operations: PatchOperation[]) => Promise<boolean>; onClose: () => void;
}) {
  const [dateError, setDateError] = useState('');
  const [saveFeedback, setSaveFeedback] = useState<'saved' | 'unchanged' | null>(null);
  useEffect(() => {
    if (!saveFeedback) return;
    const timeout = window.setTimeout(() => setSaveFeedback(null), 2400);
    return () => window.clearTimeout(timeout);
  }, [saveFeedback]);
  function saveField(event: React.FormEvent<HTMLFormElement>, group: string, field: DynamicField) {
    event.preventDefault();
    const raw = new FormData(event.currentTarget).get('value') as string;
    const value = field.type === 'number' ? Number(raw) : field.type === 'tags' ? raw.split(',').map(x => x.trim()).filter(Boolean) : raw;
    void onPatch([{ op: 'replace', path: `/additional/${pointer(group)}/fields/${pointer(field.id)}/value`, value }]);
  }
  return <section id="trip-parameters-panel" className="panel-surface relative flex h-full min-h-0 w-full flex-col border-slate-800 xl:border-r">
    <header className="chat-panel-header flex shrink-0 items-center justify-center gap-3 px-4 py-3">
      <h2 className="panel-heading-label min-w-0 flex-1 xl:max-w-[360px]">
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5 shrink-0"><path d="M4 7h16M4 17h16M8 4v6M16 14v6" /></svg>
        <span>Параметры поездки</span>
      </h2>
      <button type="button" onClick={onClose} className="panel-close-button xl:hidden">Скрыть</button>
    </header>
    <div className="app-scrollbar min-h-0 flex-1 space-y-4 overflow-y-auto p-4 pb-32">
      <form id={`trip-basic-form-${data.id}`} key={`${data.id}:${data.revision}`} className={groupCardClass} onSubmit={async event => {
        event.preventDefault();
        setSaveFeedback(null);
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
        if (operations.length === 0) { setSaveFeedback('unchanged'); return; }
        if (await onPatch(operations)) setSaveFeedback('saved');
      }}>
        <h3 className={groupHeadingClass}>Основные параметры</h3>
        <label className="block text-sm">Точка отправления<input name="departure" defaultValue={data.basic.departure} required className={inputClass} /></label>
        <label className="block text-sm">Начало поездки<input name="from" defaultValue={dateInput(data.basic.dates.from)} placeholder="ДД.ММ.ГГГГ" inputMode="numeric" className={inputClass} /></label>
        <label className="block text-sm">Конец поездки<input name="to" defaultValue={dateInput(data.basic.dates.to)} placeholder="ДД.ММ.ГГГГ" inputMode="numeric" className={inputClass} /></label>
        <label className="block text-sm">Бюджет от, ₽<input name="min" type="number" min={0} defaultValue={data.basic.price_restrictions.min < 0 ? '' : data.basic.price_restrictions.min} placeholder="Не задан" className={inputClass} /></label>
        <label className="block text-sm">Общий бюджет до, ₽<input name="max" type="number" min={0} defaultValue={data.basic.price_restrictions.max < 0 ? '' : data.basic.price_restrictions.max} placeholder="Не задан" className={inputClass} /></label>
        <p className="text-xs text-slate-400">Бюджет можно оставить пустым. {data.basic.cost_range !== '-' && `Оценка поездки: ${data.basic.cost_range}`}</p>
      </form>
      <fieldset disabled={busy}>
        <PeoplesCounter peoples={data.basic.peoples}
          onUpdateCount={(type, value) => { void onPatch([
            { op: 'replace', path: `/basic/peoples/${type}`, value },
            { op: 'replace', path: '/basic/peoples/unknown', value: 0 },
          ]); }}
          onRemoveTag={tag => { void onPatch([{ op: 'replace', path: '/basic/peoples/additional-info', value: data.basic.peoples['additional-info'].filter(x => x !== tag) }]); }} />
      </fieldset>
      <div className={groupCardClass}>
        <h3 className={groupHeadingClass}>Рассматриваемые локации</h3>
        <fieldset disabled={busy}>
          <LocationTree tree={data.basic.locations_tree} onRemovePath={path => { void onPatch([{ op: 'remove', path: '/basic/locations_tree/' + path }]); }} />
        </fieldset>
      </div>
      {data.additional.map(group => <div key={group.id} className={groupCardClass}>
        <h3 className={groupHeadingClass}>{group.title}</h3>
        <fieldset disabled={busy} className="space-y-3">
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
        </fieldset>
      </div>)}
    </div>
    <div className="trip-save-dock pointer-events-none absolute inset-x-4 bottom-4 z-10 flex flex-col items-end gap-2">
      {(dateError || error) && <p role="alert" className="pointer-events-auto max-w-sm rounded-lg bg-[var(--color-danger-bg)] px-3 py-2 text-sm text-[var(--color-danger)] shadow-md">{dateError || error}</p>}
      <button type="submit" form={`trip-basic-form-${data.id}`} disabled={busy}
        className="trip-save-button pointer-events-auto inline-flex min-w-[190px] items-center justify-center gap-3 rounded-2xl border border-white/70 bg-[var(--color-info)] px-5 py-3 text-base font-bold text-white hover:bg-[var(--color-info-hover)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-focus)] disabled:opacity-50">
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-6 w-6 shrink-0"><path d="M4 3h13l3 3v15H4V3Z" /><path d="M7 3v6h9V3M7 21v-8h10v8" /></svg>
        <span aria-live="polite" className="flex flex-col text-left leading-tight"><span>{busy ? 'Сохраняем' : saveFeedback === 'saved' ? 'Изменения' : 'Сохранить'}</span><span>{saveFeedback === 'saved' ? 'сохранены' : 'изменения'}</span></span>
      </button>
    </div>
  </section>;
}
