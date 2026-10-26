'use client';

import { type FormEvent, type KeyboardEvent } from 'react';
import type { ChatSummary } from '@/types/travel';

interface ChatListViewProps {
  chats: ChatSummary[];
  isLoading: boolean;
  listError: string | null;
  createError: string | null;
  isCreating: boolean;
  draft: string;
  onDraftChange: (value: string) => void;
  onCreate: (event: FormEvent<HTMLFormElement>) => void;
  onOpenChat: (id: number) => void;
  onRetry: () => void;
  onLogout: () => void;
}

function formatUpdatedAt(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' }).format(date);
}

export default function ChatListView({
  chats,
  isLoading,
  listError,
  createError,
  isCreating,
  draft,
  onDraftChange,
  onCreate,
  onOpenChat,
  onRetry,
  onLogout,
}: ChatListViewProps) {
  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  };

  return (
    <main className="scene-enter flex min-h-screen w-full flex-col bg-slate-950 text-slate-100 md:h-screen md:flex-row md:overflow-hidden">
      <aside className="flex h-[38vh] min-h-[300px] flex-col border-b border-slate-800 bg-slate-900/90 md:h-full md:min-h-0 md:w-[min(36vw,440px)] md:min-w-[320px] md:border-b-0 md:border-r">
        <header className="border-b border-slate-800 px-5 py-5 sm:px-7">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-blue-400">Планировщик путешествий</p>
              <h1 className="mt-1 text-2xl font-semibold tracking-tight">Мои чаты</h1>
            </div>
            <button
              type="button"
              onClick={onLogout}
              className="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-300 transition-colors hover:border-slate-500 hover:text-white"
            >
              Выйти из аккаунта
            </button>
          </div>
        </header>

        <nav aria-label="Список чатов" className="app-scrollbar min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-5">
          {isLoading ? (
            <p className="px-3 py-4 text-sm text-slate-400">Загружаем чаты…</p>
          ) : listError ? (
            <div className="px-3 py-4 text-sm text-rose-300">
              <p>{listError}</p>
              <button type="button" onClick={onRetry} className="mt-3 text-blue-400 hover:underline">Повторить</button>
            </div>
          ) : chats.length === 0 ? (
            <div className="px-3 py-4 text-sm text-slate-400">
              Здесь появятся ваши поездки. Начните с первого сообщения.
            </div>
          ) : (
            <ul className="space-y-2">
              {chats.map((chat) => (
                <li key={chat.id}>
                  <button
                    type="button"
                    onClick={() => onOpenChat(chat.id)}
                    className="group flex w-full items-start gap-3 rounded-xl border border-transparent px-3 py-3 text-left transition-colors hover:border-slate-700 hover:bg-slate-800"
                  >
                    <span aria-hidden="true" className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-800 text-lg group-hover:bg-slate-700">✦</span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-slate-100">{chat.name || 'Без названия'}</span>
                      <span className="mt-1 block text-xs text-slate-500">{formatUpdatedAt(chat.updated_at)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </nav>
      </aside>

      <section className="relative flex min-h-[62vh] flex-1 flex-col overflow-hidden bg-[radial-gradient(ellipse_at_50%_35%,rgba(37,99,235,0.13),transparent_55%)] md:min-h-0">
        <div className="flex flex-1 flex-col items-center justify-center px-6 py-10 text-center">
          <div aria-hidden="true" className="mb-7 flex h-20 w-20 items-center justify-center rounded-3xl border border-blue-500/20 bg-blue-500/10 text-4xl shadow-[0_0_80px_rgba(37,99,235,0.15)]">✈</div>
          <p className="text-xs font-semibold uppercase tracking-[0.32em] text-blue-400">Ваше следующее приключение</p>
          <h2 className="mt-4 text-4xl font-semibold tracking-tight sm:text-5xl lg:text-6xl">Пора в отпуск!</h2>
          <p className="mt-5 max-w-xl text-base leading-relaxed text-slate-400 sm:text-lg">
            Расскажите, куда хочется поехать. Поможем превратить идею в продуманный маршрут.
          </p>
        </div>

        <div className="w-full px-4 pb-5 sm:px-8 sm:pb-8 lg:px-12">
          <form onSubmit={onCreate} className="mx-auto max-w-4xl rounded-2xl border border-slate-700 bg-slate-900/95 p-4 shadow-2xl shadow-black/20 sm:p-5">
            <label htmlFor="new-chat-message" className="mb-3 block text-sm font-medium text-slate-300">Начните новый чат</label>
            <textarea
              id="new-chat-message"
              value={draft}
              onChange={(event) => onDraftChange(event.target.value)}
              onKeyDown={handleKeyDown}
              rows={4}
              maxLength={10000}
              placeholder="Например: хочу провести неделю у моря в ноябре, бюджет до 100 000 ₽…"
              className="min-h-32 w-full resize-y bg-transparent text-base leading-relaxed text-slate-100 outline-none placeholder:text-slate-500"
            />
            <div className="flex items-center justify-between gap-4 border-t border-slate-800 pt-4">
              <span className="text-xs text-slate-500">Enter — отправить · Shift + Enter — новая строка</span>
              <button
                type="submit"
                disabled={!draft.trim() || isCreating}
                className="shrink-0 rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
              >
                {isCreating ? 'Создаём…' : 'Начать поездку →'}
              </button>
            </div>
          </form>
          {createError && <p role="alert" className="mx-auto mt-3 max-w-4xl text-sm text-rose-400">{createError}</p>}
        </div>
      </section>
    </main>
  );
}
