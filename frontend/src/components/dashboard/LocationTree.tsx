'use client';

import React from 'react';
import type { LocationNode } from '@/types/travel';
import { pointer } from '@/lib/api';

interface LocationTreeProps {
  tree: LocationNode;
  onRemovePath: (fullPath: string) => void;
  currentPath?: string; // Техническое поле для сборки пути удаления
}

export default function LocationTree({ tree, onRemovePath, currentPath = '' }: LocationTreeProps) {
  // Получаем список городов/регионов на текущем уровне
  const keys = Object.keys(tree);

  if (keys.length === 0) return null;

  return (
    <ul className="space-y-2 pl-4 border-l border-slate-805/40">
      {keys.map((key) => {
        // Формируем полный путь для бэкенда (например, /basic/locations_tree/Россия/Урал)
        const newPath = currentPath ? `${currentPath}/${pointer(key)}` : pointer(key);
        const hasChildren = Object.keys(tree[key]).length > 0;

        return (
          <li key={key} className="space-y-1">
            <div className="flex items-center justify-between bg-slate-950/40 hover:bg-slate-950/80 px-3 py-1.5 rounded-lg border border-slate-850/60 transition-colors group">
              <span className="text-xs font-medium text-slate-300 flex items-center gap-1.5">
                {hasChildren ? '📁' : '📍'} {key}
              </span>
              
              {/* Кнопка удаления ветки */}
              <button
                onClick={() => onRemovePath(newPath)}
                className="text-slate-600 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer font-bold text-xs"
                title={`Удалить ${key}`}
              >
                ×
              </button>
            </div>

            {/* РЕКУРСИЯ: Если внутри есть вложенные регионы, компонент вызывает сам себя */}
            {hasChildren && (
              <div className="mt-1">
                <LocationTree 
                  tree={tree[key]} 
                  onRemovePath={onRemovePath} 
                  currentPath={newPath} 
                />
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
