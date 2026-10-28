'use client';

import { type FormEvent, useEffect, useRef, useState } from 'react';
import Image from 'next/image';
import AutoTextarea from './AutoTextarea';
import Brand from '@/components/Brand';
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
  onDeleteChat: (id: number) => Promise<void>;
  onRetry: () => void;
  onLogout: () => void;
  onOpenCart: () => void;
  cartCount: number;
}

function formatUpdatedAt(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }).format(date);
}

function updatedAtMillis(value: string) {
  const time = Date.parse(value);
  return Number.isNaN(time) ? 0 : time;
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
  onDeleteChat,
  onRetry,
  onLogout,
  onOpenCart,
  cartCount,
}: ChatListViewProps) {
  const sortedChats = [...chats].sort((a, b) => updatedAtMillis(b.updated_at) - updatedAtMillis(a.updated_at));
  const [chatToDelete, setChatToDelete] = useState<ChatSummary | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    if (!chatToDelete) return;
    const dialog = dialogRef.current;
    dialog?.showModal();
    return () => { if (dialog?.open) dialog.close(); };
  }, [chatToDelete]);

  const confirmDelete = async () => {
    if (!chatToDelete || isDeleting) return;
    setIsDeleting(true);
    setDeleteError(null);
    try {
      await onDeleteChat(chatToDelete.id);
      setChatToDelete(null);
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : 'Не удалось удалить чат');
    } finally {
      setIsDeleting(false);
    }
  };


  return (
    <main className="scene-enter flex min-h-screen w-full flex-col bg-slate-950 text-slate-100 md:h-screen md:flex-row md:overflow-hidden">
      <aside className="flex h-[38vh] min-h-[300px] flex-col border-b border-slate-800 bg-slate-900/90 md:h-full md:min-h-0 md:w-[min(36vw,440px)] md:min-w-[320px] md:border-b-0 md:border-r">
        <header className="border-b border-slate-800 px-5 py-5 sm:px-7">
          <Brand />
          <div className="mt-5 flex items-center justify-between gap-4">
            <h1 className="text-2xl font-semibold tracking-tight">Мои чаты</h1>
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" onClick={onOpenCart} className={`rounded-lg px-3 py-2 text-xs font-semibold text-white transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-focus)] ${cartCount > 0 ? 'bg-[var(--color-primary)] hover:bg-[var(--color-primary-hover)]' : 'bg-[var(--color-info)] hover:bg-[var(--color-info-hover)]'}`}>Корзина · {cartCount}</button>
              <button type="button" onClick={onLogout} className="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-300 transition-colors hover:border-blue-500 hover:text-blue-600">Выйти</button>
            </div>
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
              {sortedChats.map((chat) => (
                <li key={chat.id} className="chat-list-card group flex items-center gap-1 rounded-xl border pr-2 transition-colors">
                  <button
                    type="button"
                    onClick={() => onOpenChat(chat.id)}
                    className="flex min-w-0 flex-1 items-start gap-3 rounded-xl px-3 py-3 text-left focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--color-focus)]"
                  >
                    <span aria-hidden="true" className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--color-success-bg)] text-lg text-[var(--color-success)] group-hover:bg-[var(--color-brand-lime)]">✦</span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-slate-100">{chat.name || 'Без названия'}</span>
                      <span className="mt-1 block text-xs text-slate-500">{formatUpdatedAt(chat.updated_at)}</span>
                    </span>
                  </button>
                  <button type="button" onClick={() => { setDeleteError(null); setChatToDelete(chat); }}
                    aria-label={`Удалить чат «${chat.name || 'Без названия'}»`} title="Удалить чат"
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[var(--color-danger)] opacity-45 transition-[opacity,background-color] hover:bg-[var(--color-danger-bg)] hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-[var(--color-danger)]">
                    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5"><path d="M4 7h16M9 7V4h6v3m3 0-.8 13H6.8L6 7m4 4v5m4-5v5" /></svg>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </nav>
      </aside>

      <section className="relative flex min-h-[62vh] flex-1 flex-col overflow-hidden bg-[radial-gradient(ellipse_at_50%_35%,rgba(0,56,246,0.10),transparent_55%)] md:min-h-0">
        <div className="flex flex-1 flex-col items-center justify-center px-6 py-10 text-center">
          <div aria-hidden="true" className="mb-7 flex h-24 w-24 items-center justify-center rounded-3xl border border-blue-500/20 bg-slate-900 shadow-[0_20px_60px_rgba(0,56,246,0.12)]">
            <Image src="/brand-suitcase.svg" alt="" width={66} height={70} />
          </div>
          <p className="text-xs font-semibold uppercase tracking-[0.32em] text-blue-400">Ваше следующее приключение</p>
          <h2 className="mt-4 text-4xl font-semibold tracking-tight sm:text-5xl lg:text-6xl">Пора в отпуск!</h2>
          <p className="mt-5 max-w-xl text-base leading-relaxed text-slate-400 sm:text-lg">
            Расскажите, куда хочется поехать. Поможем превратить идею в продуманный маршрут.
          </p>
        </div>

        <div className="w-full px-4 pb-5 sm:px-8 sm:pb-8 lg:px-12">
          <form onSubmit={onCreate} className="mx-auto max-w-4xl rounded-2xl border border-slate-700 bg-slate-900/95 p-4 shadow-2xl shadow-black/20 sm:p-5">
            <label htmlFor="new-chat-message" className="mb-3 block text-sm font-medium text-slate-300">Начните новый чат</label>
            <AutoTextarea id="new-chat-message" label="Начните новый чат" value={draft} onChange={onDraftChange} disabled={isCreating}
              placeholder="Например: хочу провести неделю у моря в ноябре…"
              className="min-h-24 w-full bg-transparent py-2 text-base leading-relaxed text-slate-100 outline-none placeholder:text-slate-500" />
            <div className="flex items-center justify-between gap-4 border-t border-slate-800 pt-4">
              <span className="text-xs text-slate-500">Enter — отправить · Shift + Enter — новая строка</span>
              <button
                type="submit"
                disabled={!draft.trim() || isCreating}
                className="shrink-0 rounded-xl bg-[var(--color-brand-green)] px-5 py-2.5 text-sm font-semibold text-[var(--color-brand-navy)] transition-colors hover:bg-[var(--color-secondary-hover)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-brand-green)] disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
              >
                {isCreating ? 'Создаём…' : 'Начать поездку →'}
              </button>
            </div>
          </form>
          {createError && <p role="alert" className="mx-auto mt-3 max-w-4xl text-sm text-rose-400">{createError}</p>}
        </div>
      </section>
      {chatToDelete && <dialog ref={dialogRef} aria-labelledby="delete-chat-title" aria-describedby="delete-chat-description"
        onCancel={event => { event.preventDefault(); if (!isDeleting) setChatToDelete(null); }}
        onClick={event => { if (event.target === event.currentTarget && !isDeleting) setChatToDelete(null); }}
        className="delete-chat-dialog m-auto w-[min(420px,calc(100%-32px))] rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-6 text-[var(--color-text)] shadow-2xl">
        <h2 id="delete-chat-title" className="text-xl font-bold">Удалить чат?</h2>
        <p id="delete-chat-description" className="mt-3 text-sm leading-relaxed text-[var(--color-text-secondary)]">«{chatToDelete.name || 'Без названия'}» и его история будут удалены без возможности восстановления.</p>
        {deleteError && <p role="alert" className="mt-3 rounded-lg bg-[var(--color-danger-bg)] px-3 py-2 text-sm text-[var(--color-danger)]">{deleteError}</p>}
        <div className="mt-6 flex justify-end gap-2">
          <button type="button" disabled={isDeleting} onClick={() => setChatToDelete(null)} className="rounded-xl border border-[var(--color-border)] px-4 py-2.5 text-sm font-semibold transition-colors hover:bg-[var(--color-surface-muted)] disabled:opacity-50">Отмена</button>
          <button type="button" disabled={isDeleting} onClick={() => void confirmDelete()} className="rounded-xl bg-[var(--color-danger)] px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:brightness-90 disabled:opacity-50">{isDeleting ? 'Удаляем…' : 'Удалить чат'}</button>
        </div>
      </dialog>}
    </main>
  );
}
