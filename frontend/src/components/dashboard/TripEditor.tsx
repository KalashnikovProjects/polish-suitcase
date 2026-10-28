'use client';

import type { DynamicField, LocationNode, PatchOperation, TravelResponse } from '@/types/travel';
import { pointer } from '@/lib/api';
import { dateInput, parseDateInput } from '@/lib/presentation';
import { useEffect, useState } from 'react';
import Image from 'next/image';
import PeoplesCounter from './PeoplesCounter';
import LocationTree from './LocationTree';
import { groupCardClass, groupHeadingClass } from './groupStyles';

const inputClass = 'mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100';
type DraftValue = string | number | boolean;
type LocationEdit = { op: 'add' | 'remove'; path: string };

function editedLocations(tree: LocationNode, edits: LocationEdit[]): LocationNode {
  const result = JSON.parse(JSON.stringify(tree)) as LocationNode;
  for (const edit of edits) {
    const parts = edit.path.split('/').map(part => part.replace(/~1/g, '/').replace(/~0/g, '~'));
    let parent: LocationNode | undefined = result;
    for (const part of parts.slice(0, -1)) parent = parent?.[part];
    if (!parent) continue;
    if (edit.op === 'remove') delete parent[parts[parts.length - 1]];
    else parent[parts[parts.length - 1]] ??= {};
  }
  return result;
}

function locationExists(tree: LocationNode, path: string): boolean {
  let node = tree;
  for (const segment of path.split('/')) {
    const name = segment.replace(/~1/g, '/').replace(/~0/g, '~');
    if (!Object.prototype.hasOwnProperty.call(node, name)) return false;
    node = node[name];
  }
  return true;
}

function fieldValue(field: DynamicField, raw: DraftValue): string | number | boolean | string[] {
  if (field.type === 'number') return Number(raw);
  if (field.type === 'tags') return String(raw).split(',').map(value => value.trim()).filter(Boolean);
  if (field.type === 'boolean') return Boolean(raw);
  return String(raw);
}

export default function TripEditor({ data, busy, error, onPatch, onClose }: {
  data: TravelResponse; busy: boolean; error: string | null; onPatch: (operations: PatchOperation[]) => Promise<boolean>; onClose: () => void;
}) {
  const [draft, setDraft] = useState<Record<string, DraftValue>>({});
  const [removedTags, setRemovedTags] = useState<string[]>([]);
  const [locationEdits, setLocationEdits] = useState<LocationEdit[]>([]);
  const [locationError, setLocationError] = useState('');
  const [dateError, setDateError] = useState('');
  const [saveFeedback, setSaveFeedback] = useState<'saved' | 'unchanged' | null>(null);

  useEffect(() => {
    if (!saveFeedback) return;
    const timeout = window.setTimeout(() => setSaveFeedback(null), 2400);
    return () => window.clearTimeout(timeout);
  }, [saveFeedback]);

  function edit(path: string, value: DraftValue) {
    setDraft(current => ({ ...current, [path]: value }));
    setDateError('');
    setSaveFeedback(null);
  }

  function rawValue(path: string, original: DraftValue): DraftValue {
    return draft[path] ?? original;
  }

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setSaveFeedback(null);
    const operations: PatchOperation[] = [];
    const addIfChanged = (path: string, value: string | number | boolean, original: string | number | boolean) => {
      if (value !== original) operations.push({ op: 'replace', path, value });
    };

    const departure = String(rawValue('/basic/departure', data.basic.departure));
    const start = parseDateInput(String(rawValue('/basic/dates/from', dateInput(data.basic.dates.from))));
    const end = parseDateInput(String(rawValue('/basic/dates/to', dateInput(data.basic.dates.to))));
    if (start === null || end === null) {
      setDateError('Введите существующую дату в формате ДД.ММ.ГГГГ');
      return;
    }
    setDateError('');
    addIfChanged('/basic/departure', departure, data.basic.departure);
    addIfChanged('/basic/dates/from', start, data.basic.dates.from);
    addIfChanged('/basic/dates/to', end, data.basic.dates.to);
    const min = rawValue('/basic/price_restrictions/min', data.basic.price_restrictions.min < 0 ? '' : String(data.basic.price_restrictions.min));
    const max = rawValue('/basic/price_restrictions/max', data.basic.price_restrictions.max < 0 ? '' : String(data.basic.price_restrictions.max));
    addIfChanged('/basic/price_restrictions/min', String(min).trim() ? Number(min) : -1, data.basic.price_restrictions.min);
    addIfChanged('/basic/price_restrictions/max', String(max).trim() ? Number(max) : -1, data.basic.price_restrictions.max);

    for (const type of ['adults', 'children'] as const) {
      const path = `/basic/peoples/${type}`;
      addIfChanged(path, Number(rawValue(path, data.basic.peoples[type])), data.basic.peoples[type]);
    }
    if (operations.some(op => op.path === '/basic/peoples/adults' || op.path === '/basic/peoples/children') && data.basic.peoples.unknown !== 0) {
      operations.push({ op: 'replace', path: '/basic/peoples/unknown', value: 0 });
    }
    const remainingTags = data.basic.peoples['additional-info'].filter(tag => !removedTags.includes(tag));
    if (remainingTags.length !== data.basic.peoples['additional-info'].length) {
      operations.push({ op: 'replace', path: '/basic/peoples/additional-info', value: remainingTags });
    }
    const locations = editedLocations(data.basic.locations_tree, locationEdits);
    if (JSON.stringify(locations) !== JSON.stringify(data.basic.locations_tree)) {
      operations.push({ op: 'replace', path: '/basic/locations_tree', value: locations });
    }

    for (const group of data.additional) for (const field of group.fields) {
      const path = `/additional/${pointer(group.id)}/fields/${pointer(field.id)}/value`;
      if (!Object.prototype.hasOwnProperty.call(draft, path)) continue;
      const value = fieldValue(field, draft[path]);
      if (JSON.stringify(value) !== JSON.stringify(field.value)) operations.push({ op: 'replace', path, value });
    }
    if (operations.some(op => op.path.startsWith('/basic/price_restrictions/'))) {
      const budgetFields = data.additional.find(group => group.id === 'travel_details')?.fields ?? [];
      for (const id of ['budget_per_person', 'budget_unlimited']) {
        const field = budgetFields.find(item => item.id === id);
        const path = `/additional/travel_details/fields/${id}/value`;
        if (field?.value === true && !Object.prototype.hasOwnProperty.call(draft, path)) {
          operations.push({ op: 'replace', path, value: false });
        }
      }
    }

    if (operations.length === 0) {
      setSaveFeedback('unchanged');
      return;
    }
    if (await onPatch(operations)) {
      setDraft({});
      setRemovedTags([]);
      setLocationEdits([]);
      setLocationError('');
      setSaveFeedback('saved');
    }
  }

  const people = {
    ...data.basic.peoples,
    adults: Number(rawValue('/basic/peoples/adults', data.basic.peoples.adults)),
    children: Number(rawValue('/basic/peoples/children', data.basic.peoples.children)),
    'additional-info': data.basic.peoples['additional-info'].filter(tag => !removedTags.includes(tag)),
  };
  const locations = editedLocations(data.basic.locations_tree, locationEdits);

  function addLocation(parentPath: string, name: string): boolean {
    const clean = name.trim();
    if (!clean) { setLocationError('Введите название локации'); return false; }
    if (clean.length > 100) { setLocationError('Название должно быть короче 100 символов'); return false; }
    const path = parentPath ? `${parentPath}/${pointer(clean)}` : pointer(clean);
    if (locationExists(locations, path)) { setLocationError('Такая локация уже есть в этой ветви'); return false; }
    if (parentPath && !locationExists(locations, parentPath)) { setLocationError('Родительская локация уже удалена'); return false; }
    setLocationEdits(current => [...current, { op: 'add', path }]);
    setLocationError('');
    setSaveFeedback(null);
    return true;
  }

  return <section id="trip-parameters-panel" className="panel-surface relative flex h-full min-h-0 w-full flex-col border-slate-800 xl:border-r">
    <header className="chat-panel-header flex shrink-0 items-center justify-center gap-3 px-4 py-3">
      <h2 className="panel-heading-label min-w-0 flex-1 xl:max-w-[360px]">
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5 shrink-0"><path d="M4 7h16M4 17h16M8 4v6M16 14v6" /></svg>
        <span>Параметры поездки</span>
      </h2>
      <button type="button" onClick={onClose} className="panel-close-button xl:hidden">Скрыть</button>
    </header>
    <form id={`trip-basic-form-${data.id}`} className="app-scrollbar min-h-0 flex-1 overflow-y-auto p-4 pb-32" onSubmit={save}>
      <fieldset disabled={busy} className="space-y-4">
        <div className="rounded-2xl border border-[var(--color-border)] bg-[radial-gradient(ellipse_at_50%_0%,rgba(0,56,246,0.10),rgba(255,255,255,0.9)_75%)] px-4 py-5 text-center shadow-sm">
          <span className="mx-auto mb-3 flex h-14 w-14 items-center justify-center rounded-2xl border border-[var(--color-border)] bg-white shadow-[0_10px_24px_rgba(0,56,246,0.10)]">
            <Image src="/brand-suitcase.svg" alt="" width={38} height={40} />
          </span>
          <p className="mx-auto max-w-[34ch] text-base font-semibold leading-snug text-[var(--color-brand-navy)]">Здесь вы можете уточнять параметры поездки вручную.</p>
          <p className="mx-auto mt-2 max-w-[38ch] text-xs leading-relaxed text-[var(--color-text-secondary)]">Можете также написать в чат — нейросеть заполнит эти поля сама.</p>
        </div>
        <div className={groupCardClass}>
          <h3 className={groupHeadingClass}>Основные параметры</h3>
          <label className="block text-sm">Точка отправления<input value={String(rawValue('/basic/departure', data.basic.departure))} onChange={event => edit('/basic/departure', event.target.value)} required className={inputClass} /></label>
          <label className="block text-sm">Начало поездки<input value={String(rawValue('/basic/dates/from', dateInput(data.basic.dates.from)))} onChange={event => edit('/basic/dates/from', event.target.value)} placeholder="ДД.ММ.ГГГГ" inputMode="numeric" className={inputClass} /></label>
          <label className="block text-sm">Конец поездки<input value={String(rawValue('/basic/dates/to', dateInput(data.basic.dates.to)))} onChange={event => edit('/basic/dates/to', event.target.value)} placeholder="ДД.ММ.ГГГГ" inputMode="numeric" className={inputClass} /></label>
          <label className="block text-sm">Бюджет от, ₽<input type="number" min={0} value={String(rawValue('/basic/price_restrictions/min', data.basic.price_restrictions.min < 0 ? '' : String(data.basic.price_restrictions.min)))} onChange={event => edit('/basic/price_restrictions/min', event.target.value)} placeholder="Не задан" className={inputClass} /></label>
          <label className="block text-sm">Общий бюджет до, ₽<input type="number" min={0} value={String(rawValue('/basic/price_restrictions/max', data.basic.price_restrictions.max < 0 ? '' : String(data.basic.price_restrictions.max)))} onChange={event => edit('/basic/price_restrictions/max', event.target.value)} placeholder="Не задан" className={inputClass} /></label>
          <p className="text-xs text-slate-400">Бюджет можно оставить пустым. {data.basic.cost_range !== '-' && `Оценка поездки: ${data.basic.cost_range}`}</p>
        </div>
        <PeoplesCounter peoples={people}
          onUpdateCount={(type, value) => edit(`/basic/peoples/${type}`, value)}
          onRemoveTag={tag => { setRemovedTags(current => [...current, tag]); setSaveFeedback(null); }} />
        <div className={groupCardClass}>
          <h3 className={groupHeadingClass}>Рассматриваемые локации</h3>
          <LocationTree tree={locations} onAddPath={addLocation} onRemovePath={path => {
            setLocationEdits(current => [...current, { op: 'remove', path }]);
            setLocationError('');
            setSaveFeedback(null);
          }} />
          {locationError && <p role="alert" className="mt-2 text-xs text-rose-300">{locationError}</p>}
        </div>
        {data.additional.map(group => <div key={group.id} className={groupCardClass}>
          <h3 className={groupHeadingClass}>{group.title}</h3>
          <div className="space-y-3">
            {group.fields.map(field => {
              const path = `/additional/${pointer(group.id)}/fields/${pointer(field.id)}/value`;
              if (field.type === 'boolean') return <label key={field.id} className="flex gap-3 text-sm"><input type="checkbox" checked={Boolean(rawValue(path, Boolean(field.value)))} onChange={event => edit(path, event.target.checked)} />{field.label}</label>;
              const value = String(rawValue(path, Array.isArray(field.value) ? field.value.join(', ') : String(field.value)));
              return <label key={field.id} className="block text-sm">{field.label}
                {field.id === 'autonomy' ? <select value={value} onChange={event => edit(path, event.target.value)} className={inputClass}><option value="balanced">Предлагать варианты сразу</option><option value="autonomous">Подбирать самостоятельно</option><option value="guided">Уточнять перед поиском</option></select> :
                  field.id === 'meal_plan' ? <select value={value} onChange={event => edit(path, event.target.value)} className={inputClass}><option value="">Не задано</option><option value="none">Без питания</option><option value="breakfast">Завтрак</option><option value="half_board">Полупансион</option><option value="full_board">Полный пансион</option><option value="all_inclusive">Всё включено</option></select> : <input type={field.type === 'number' ? 'number' : 'text'} required={field.type === 'number'} min={field.id === 'rooms' ? 1 : field.type === 'number' ? 0 : undefined} max={field.id === 'rooms' ? 10 : undefined} value={value} onChange={event => edit(path, event.target.value)} className={inputClass} />}
              </label>;
            })}
          </div>
        </div>)}
      </fieldset>
    </form>
    <div className="trip-save-dock pointer-events-none absolute inset-x-4 bottom-4 z-10 flex flex-col items-end gap-2">
      {(dateError || error) && <p role="alert" className="pointer-events-auto max-w-sm rounded-lg bg-[var(--color-danger-bg)] px-3 py-2 text-sm text-[var(--color-danger)] shadow-md">{dateError || error}</p>}
      <button type="submit" form={`trip-basic-form-${data.id}`} disabled={busy}
        className="trip-save-button pointer-events-auto inline-flex min-w-[190px] items-center justify-center gap-3 rounded-2xl border border-white/70 bg-[var(--color-info)] px-5 py-3 text-base font-bold text-white hover:bg-[var(--color-info-hover)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-focus)] disabled:opacity-50">
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-6 w-6 shrink-0"><path d="M4 3h13l3 3v15H4V3Z" /><path d="M7 3v6h9V3M7 21v-8h10v8" /></svg>
        <span aria-live="polite" className="flex flex-col text-left leading-tight"><span>{busy ? 'Сохраняем' : saveFeedback === 'saved' ? 'Изменения' : saveFeedback === 'unchanged' ? 'Нет' : 'Сохранить'}</span><span>{saveFeedback === 'saved' ? 'сохранены' : saveFeedback === 'unchanged' ? 'изменений' : 'изменения'}</span></span>
      </button>
    </div>
  </section>;
}
