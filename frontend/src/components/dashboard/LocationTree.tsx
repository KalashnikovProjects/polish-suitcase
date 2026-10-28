'use client';

import { useState } from 'react';
import type { LocationNode } from '@/types/travel';
import { pointer } from '@/lib/api';

interface LocationTreeProps {
  tree: LocationNode;
  onRemovePath: (fullPath: string) => void;
  onAddPath: (parentPath: string, name: string) => boolean;
  currentPath?: string;
}

export default function LocationTree({ tree, onRemovePath, onAddPath, currentPath = '' }: LocationTreeProps) {
  const keys = Object.keys(tree);
  const [addingTo, setAddingTo] = useState<string | null>(null);
  const [newName, setNewName] = useState('');

  function add(parentPath: string) {
    if (onAddPath(parentPath, newName)) {
      setNewName('');
      setAddingTo(null);
    }
  }

  return (
    <div className="space-y-2">
    {keys.length === 0 && !currentPath && <p className="text-xs text-slate-500">Пока нет локаций. Добавьте первую.</p>}
    {keys.length > 0 && <ul className="space-y-2 border-l border-slate-700/60 pl-4">
      {keys.map((key) => {
        const newPath = currentPath ? `${currentPath}/${pointer(key)}` : pointer(key);
        const hasChildren = Object.keys(tree[key]).length > 0;

        return (
          <li key={key} className="space-y-1">
            <div className="group flex items-center justify-between rounded-lg border border-slate-700/60 bg-slate-950/40 px-3 py-1.5 transition-colors hover:bg-slate-800/80">
              <span className="text-xs font-medium text-slate-300 flex items-center gap-1.5">
                {hasChildren ? '📁' : '📍'} {key}
              </span>
              
              <div className="flex items-center gap-1">
                <button type="button" onClick={() => { setAddingTo(newPath); setNewName(''); }}
                  className="rounded-md bg-slate-800 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700"
                  aria-label={`Добавить дочернюю локацию к ${key}`} title={`Добавить внутри ${key}`}>+</button>
                <button type="button" onClick={() => onRemovePath(newPath)}
                  className="rounded-md bg-slate-800 px-2 py-1 text-xs text-rose-300 hover:bg-rose-950"
                  aria-label={`Удалить ${key}`} title={`Удалить ${key}`}>×</button>
              </div>
            </div>
            {addingTo === newPath && <div className="flex gap-2 pl-3">
              <input autoFocus value={newName} onChange={event => setNewName(event.target.value)}
                onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); add(newPath); } }}
                placeholder="Новая локация" aria-label={`Новая локация внутри ${key}`}
                className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-100" />
              <button type="button" onClick={() => add(newPath)} className="rounded-lg bg-blue-700 px-3 text-sm text-white">Добавить</button>
              <button type="button" onClick={() => setAddingTo(null)} className="rounded-lg bg-slate-800 px-2 text-sm text-slate-200" aria-label="Отменить добавление">×</button>
            </div>}
            {hasChildren && (
              <div className="mt-1">
                <LocationTree 
                  tree={tree[key]} 
                  onRemovePath={onRemovePath} 
                  onAddPath={onAddPath}
                  currentPath={newPath} 
                />
              </div>
            )}
          </li>
        );
      })}
    </ul>}
    {!currentPath && <div className="flex gap-2 pt-2">
      <input value={addingTo === '' ? newName : ''}
        onFocus={() => { if (addingTo !== '') { setAddingTo(''); setNewName(''); } }}
        onChange={event => { setAddingTo(''); setNewName(event.target.value); }}
        onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); add(''); } }}
        placeholder="Новая корневая локация" aria-label="Новая корневая локация"
        className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-100" />
      <button type="button" onClick={() => add('')} className="rounded-lg bg-blue-700 px-3 text-sm text-white">Добавить</button>
    </div>}
    </div>
  );
}
