'use client';

import React from 'react';

export default function Home() {
  return (
    <main className="flex h-screen w-screen bg-slate-900 text-slate-100 overflow-hidden">
      
      {/* 1. ЛЕВАЯ КОЛОНКА: Чат с ИИ-агентом */}
      <section className="w-[30%] h-full border-r border-slate-800 flex flex-col bg-slate-900/50">
        <div className="p-4 border-b border-slate-800 font-semibold text-sm tracking-wide text-slate-400">
          ЧАТ С АГЕНТОМ
        </div>
        <div className="flex-1 p-4 overflow-y-auto">
          {/* Здесь будут крутиться сообщения через .map() */}
          <p className="text-sm text-slate-500 italic">Лента сообщений пока пуста...</p>
        </div>
        <div className="p-4 border-t border-slate-800">
          <div className="w-full bg-slate-800 text-sm p-3 rounded-xl text-slate-400">
            Поле ввода сообщения...
          </div>
        </div>
      </section>

      {/* 2. ЦЕНТРАЛЬНАЯ КОЛОНКА: Конструктор параметров поездки */}
      <section className="w-[40%] h-full border-r border-slate-800 flex flex-col bg-slate-950">
        <div className="p-4 border-b border-slate-800 font-semibold text-sm tracking-wide text-slate-400">
          ПАРАМЕТРЫ ПОЕЗДКИ (BASIC)
        </div>
        <div className="flex-1 p-6 overflow-y-auto space-y-6">
          <div className="p-4 bg-slate-900 border border-slate-800 rounded-xl">
            <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Отправление</h3>
            <p className="text-sm">Тут будет локация отправления</p>
          </div>
          <div className="p-4 bg-slate-900 border border-slate-800 rounded-xl">
            <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Дерево локаций</h3>
            <p className="text-sm text-slate-500 italic">Загрузка дерева...</p>
          </div>
        </div>
      </section>

      {/* 3. ПРАВАЯ КОЛОНКА: Варианты маршрутов */}
      <section className="w-[30%] h-full flex flex-col bg-slate-900/30">
        <div className="p-4 border-b border-slate-800 font-semibold text-sm tracking-wide text-slate-400">
          ВАРИАНТЫ МАРШРУТОВ (VARIANTS)
        </div>
        <div className="flex-1 p-4 overflow-y-auto space-y-4">
          <div className="p-4 bg-slate-900/60 border border-slate-800/80 rounded-xl animate-pulse">
            <p className="text-sm text-slate-400">⚙️ ИИ обдумывает варианты...</p>
          </div>
        </div>
      </section>

    </main>
  );
}
