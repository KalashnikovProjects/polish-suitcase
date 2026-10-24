'use client';

import React from 'react';

interface PeoplesProps {
  peoples: {
    adults: number;
    children: number;
    unknown: number;
    'additional-info': string[];
  };
  onRemoveTag: (tagValue: string) => void;
  onUpdateCount: (type: 'adults' | 'children', newValue: number) => void;
}

export default function PeoplesCounter({ peoples, onRemoveTag, onUpdateCount }: PeoplesProps) {
  return (
    <div className="p-4 bg-slate-900 border border-slate-800 rounded-xl space-y-4">
      <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wider">Состав группы</h3>
      
      {/* Ряды счетчиков */}
      <div className="space-y-3">
        {/* Взрослые */}
        <div className="flex items-center justify-between">
          <span className="text-sm text-slate-300">Взрослые</span>
          <div className="flex items-center gap-3 bg-slate-950 px-2 py-1 rounded-lg border border-slate-800">
            <button 
              onClick={() => onUpdateCount('adults', Math.max(0, peoples.adults - 1))}
              className="text-slate-500 hover:text-slate-200 cursor-pointer font-bold w-6 h-6 flex items-center justify-center"
            >
              −
            </button>
            <span className="text-sm text-slate-200 w-4 text-center font-medium">{peoples.adults}</span>
            <button 
              onClick={() => onUpdateCount('adults', peoples.adults + 1)}
              className="text-slate-500 hover:text-slate-200 cursor-pointer font-bold w-6 h-6 flex items-center justify-center"
            >
              +
            </button>
          </div>
        </div>

        {/* Дети */}
        <div className="flex items-center justify-between">
          <span className="text-sm text-slate-300">Дети</span>
          <div className="flex items-center gap-3 bg-slate-950 px-2 py-1 rounded-lg border border-slate-800">
            <button 
              onClick={() => onUpdateCount('children', Math.max(0, peoples.children - 1))}
              className="text-slate-500 hover:text-slate-200 cursor-pointer font-bold w-6 h-6 flex items-center justify-center"
            >
              −
            </button>
            <span className="text-sm text-slate-200 w-4 text-center font-medium">{peoples.children}</span>
            <button 
              onClick={() => onUpdateCount('children', peoples.children + 1)}
              className="text-slate-500 hover:text-slate-200 cursor-pointer font-bold w-6 h-6 flex items-center justify-center"
            >
              +
            </button>
          </div>
        </div>

        {/* Подсказка от ИИ (unknown) */}
        {peoples.unknown > 0 && (
          <div className="text-[11px] text-amber-400 bg-amber-950/20 border border-amber-900/40 p-2 rounded-lg italic">
            💡 ИИ предполагает присутствие еще {peoples.unknown} чел. (уточните в чате или счетчиками).
          </div>
        )}
      </div>

      {/* Блок интерактивных «овальчиков»-тегов */}
      {peoples['additional-info'].length > 0 && (
        <div className="pt-2 border-t border-slate-800/60">
          <h4 className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-2">Особые условия:</h4>
          <div className="flex flex-wrap gap-2">
            {peoples['additional-info'].map((info, idx) => (
              <span 
                key={idx} 
                className="text-xs bg-blue-950/40 border border-blue-800/60 text-blue-400 pl-3 pr-2 py-1 rounded-full flex items-center gap-1.5 transition-colors hover:border-red-900/60 hover:bg-red-950/20 group"
              >
                {info}
                <button 
                  onClick={() => onRemoveTag(info)}
                  className="text-blue-500 group-hover:text-red-400 cursor-pointer font-bold text-sm leading-none focus:outline-none"
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
