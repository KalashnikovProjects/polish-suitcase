'use client';

import React, { useCallback, useEffect, useState } from 'react';
import type { ChatMessage, ChatSummary, TravelResponse, PatchOperation } from '@/types/travel';
import RegisterForm from '@/components/auth/RegisterForm';
import LoginForm from '@/components/auth/LoginForm';
import ChatListView from '@/components/chat/ChatListView';
import MessageText from '@/components/chat/MessageText';
import { useChatSSE } from '../hooks/useChatSSE';
import TripEditor from '@/components/dashboard/TripEditor';
import Variants from '@/components/dashboard/Variants';
import { apiRequest, refreshSession, invalidateSession } from '@/lib/api';

async function responseError(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    return body?.error?.message || fallback;
  } catch {
    return fallback;
  }
}

function chatNameFromMessage(message: string): string {
  const firstLine = message.split(/\r?\n/)[0].trim();
  return (firstLine || message).slice(0, 80);
}

const restoreSession = refreshSession;

export default function Home() {
  const [token, setToken] = useState<string | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [authScreen, setAuthScreen] = useState<'login' | 'register'>('login');
  const [selectedChatId, setSelectedChatId] = useState<number | null>(null);
  const [data, setData] = useState<TravelResponse | null>(null);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [chatError, setChatError] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [isEditingName, setIsEditingName] = useState(false);
  const [isSavingName, setIsSavingName] = useState(false);
  const [nameDraft, setNameDraft] = useState('');
  const [nameError, setNameError] = useState<string | null>(null);
  const [newChatDraft, setNewChatDraft] = useState('');
  const [inputValue, setInputValue] = useState('');
  const [listReloadKey, setListReloadKey] = useState(0);
  const [chatReloadKey, setChatReloadKey] = useState(0);
  const [isPatching, setIsPatching] = useState(false);
  const updateToken = useCallback((value: string | null) => {
    setToken(value);
    if (!value) {
      invalidateSession();
      setData(null);
      setSelectedChatId(null);
      setChats([]);
      window.sessionStorage.removeItem('travel-chat');
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    restoreSession()
      .then((accessToken) => {
        if (!cancelled && accessToken) {
          setToken(accessToken);
          const remembered = Number(window.sessionStorage.getItem('travel-chat'));
          if (Number.isSafeInteger(remembered) && remembered > 0) setSelectedChatId(remembered);
        }
      })
      .finally(() => { if (!cancelled) setAuthReady(true); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!token) return;
    let active = true;
    const timer = window.setInterval(() => {
      refreshSession().then(value => { if (active) updateToken(value); });
    }, 25 * 60 * 1000);
    return () => { active = false; window.clearInterval(timer); };
  }, [token, updateToken]);

  useEffect(() => {
    if (!token || selectedChatId !== null) return;
    let cancelled = false;
    apiRequest('/api/v1/chats?limit=100', token, {}, updateToken)
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseError(response, 'Не удалось загрузить чаты'));
        return response.json() as Promise<ChatSummary[]>;
      })
      .then((items) => { if (!cancelled) setChats(items); })
      .catch((error) => { if (!cancelled) setListError(error.message); })
      .finally(() => { if (!cancelled) setListLoading(false); });
    return () => { cancelled = true; };
  }, [token, selectedChatId, listReloadKey, updateToken]);

  useEffect(() => {
    if (!token || selectedChatId === null) return;
    let cancelled = false;
    apiRequest(`/api/v1/chats/${selectedChatId}`, token, {}, updateToken)
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseError(response, 'Не удалось открыть чат'));
        return response.json() as Promise<TravelResponse>;
      })
      .then((chat) => { if (!cancelled) setData((current) => current?.id === chat.id ? current : chat); })
      .catch((error) => { if (!cancelled) setChatError(error.message); });
    return () => { cancelled = true; };
  }, [token, selectedChatId, chatReloadKey, updateToken]);

  useChatSSE({
    chatId: selectedChatId ?? 0,
    token: selectedChatId === null ? null : token,
    onError: setChatError,
    onUnauthorized: async signal => {
      const renewed = await refreshSession();
      if (!signal.aborted) updateToken(renewed);
    },
    onInitInfo: chat => setData(current => current?.id === chat.id && current.revision > chat.revision ? current : chat),
    onNewMessage: (message: ChatMessage) => {
      setData((current) => {
        if (!current || current.history.some((item) => item.id === message.id)) return current;
        return { ...current, history: [...current.history, message] };
      });
    },
    onChangedTravel: (update) => {
      setData((current) => {
        if (!current || update.revision <= current.revision) return current;
        return {
          ...current,
          revision: update.revision,
          chat: update.chat,
          basic: update.basic,
          variants: update.variants,
          additional: update.additional,
        };
      });
    },
    onAgentInfo: (agent) => {
      setData((current) => current ? {
        ...current,
        agent: {
          status: agent.status as TravelResponse['agent']['status'],
          'status-message': agent['status-message'],
        },
      } : current);
    },
  });

  const openChat = (id: number) => {
    window.sessionStorage.setItem('travel-chat', String(id));
    setSelectedChatId(id);
    setData(null);
    setChatError(null);
    setSendError(null);
    setInputValue('');
    setIsEditingName(false);
    setNameError(null);
  };

  const returnToList = () => {
    window.sessionStorage.removeItem('travel-chat');
    setSelectedChatId(null);
    setData(null);
    setChatError(null);
    setSendError(null);
    setIsEditingName(false);
    setNameError(null);
    setListLoading(true);
    setListError(null);
  };

  const retryList = () => {
    setListLoading(true);
    setListError(null);
    setListReloadKey((key) => key + 1);
  };

  const retryChat = () => {
    setChatError(null);
    setChatReloadKey((key) => key + 1);
  };

  const handleCreateChat = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const message = newChatDraft.trim();
    if (!message || !token || isCreating) return;
    setIsCreating(true);
    setCreateError(null);
    try {
      const response = await apiRequest('/api/v1/chats', token, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: chatNameFromMessage(message), message }),
      }, updateToken);
      if (!response.ok) throw new Error(await responseError(response, 'Не удалось создать чат'));
      const created: { id: number; name: string } = await response.json();
      setChats((current) => [{ id: created.id, name: created.name, updated_at: new Date().toISOString() }, ...current]);
      setNewChatDraft('');
      openChat(created.id);
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : 'Не удалось создать чат');
    } finally {
      setIsCreating(false);
    }
  };

  const handleSendMessage = async (event: React.FormEvent) => {
    event.preventDefault();
    const message = inputValue.trim();
    if (!message || !token || !data || isSending) return;
    setInputValue('');
    setIsSending(true);
    setSendError(null);
    try {
      const response = await apiRequest(`/api/v1/chats/${data.id}/messages`, token, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ message }),
      }, updateToken);
      if (!response.ok) throw new Error(await responseError(response, 'Не удалось отправить сообщение'));
    } catch (error) {
      setInputValue(message);
      setSendError(error instanceof Error ? error.message : 'Не удалось отправить сообщение');
    } finally {
      setIsSending(false);
    }
  };

  const handleRenameChat = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = nameDraft.trim();
    if (!token || !data || !name || isSavingName) return;
    if (name === data.chat.name) {
      setIsEditingName(false);
      return;
    }
    setIsSavingName(true);
    setNameError(null);
    try {
      const response = await apiRequest(`/api/v1/chats/${data.id}/messages`, token, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          from_revision: data.revision,
          operations: [{ op: 'replace', path: '/chat/name', value: name }],
        }),
      }, updateToken);
      if (!response.ok) throw new Error(await responseError(response, 'Не удалось переименовать чат'));
      setIsEditingName(false);
    } catch (error) {
      setNameError(error instanceof Error ? error.message : 'Не удалось переименовать чат');
    } finally {
      setIsSavingName(false);
    }
  };

  const handleLogout = async () => {
    try {
      const response = await fetch('/api/v1/auth/logout', { method: 'POST', credentials: 'same-origin' });
      if (!response.ok) throw new Error('Не удалось выйти из аккаунта');
      invalidateSession();
      setToken(null);
      setChats([]);
      returnToList();
    } catch (error) {
      setListError(error instanceof Error ? error.message : 'Не удалось выйти из аккаунта');
    }
  };

  const handlePatch = async (operations: PatchOperation[]) => {
    if (!token || !data || isPatching) return;
    const chatId = data.id;
    setIsPatching(true);
    setSendError(null);
    try {
      const response = await apiRequest(`/api/v1/chats/${chatId}/messages`, token, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from_revision: data.revision, operations }),
      }, updateToken);
      if (response.status === 409) {
        const latest = await apiRequest(`/api/v1/chats/${chatId}`, token, {}, updateToken);
        if (latest.ok) {
          const snapshot: TravelResponse = await latest.json();
          setData(current => current?.id === chatId && snapshot.revision >= current.revision ? snapshot : current);
        }
        throw new Error('Параметры уже изменились. Проверьте актуальные значения и повторите действие.');
      }
      if (!response.ok) throw new Error(await responseError(response, 'Не удалось сохранить параметры'));
    } catch (error) {
      setSendError(error instanceof Error ? error.message : 'Не удалось сохранить параметры');
    } finally { setIsPatching(false); }
  };

  if (!authReady) {
    return <main className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-400">Загружаем приложение…</main>;
  }

  if (!token) {
    return (
      <main className="flex h-screen w-screen items-center justify-center bg-slate-950 p-4 text-slate-100">
        {authScreen === 'login' ? (
          <LoginForm onSuccess={(accessToken) => { invalidateSession(); setListLoading(true); setListError(null); setToken(accessToken); }} onSwitchToRegister={() => setAuthScreen('register')} />
        ) : (
          <RegisterForm onSuccess={() => setAuthScreen('login')} onSwitchToLogin={() => setAuthScreen('login')} />
        )}
      </main>
    );
  }

  if (selectedChatId === null) {
    return (
      <ChatListView
        chats={chats}
        isLoading={listLoading}
        listError={listError}
        createError={createError}
        isCreating={isCreating}
        draft={newChatDraft}
        onDraftChange={setNewChatDraft}
        onCreate={handleCreateChat}
        onOpenChat={openChat}
        onRetry={retryList}
        onLogout={handleLogout}
      />
    );
  }

  if (!data || data.id !== selectedChatId) {
    return (
      <main className="scene-enter flex min-h-screen flex-col items-center justify-center gap-5 bg-slate-950 bg-[radial-gradient(ellipse_at_50%_35%,rgba(37,99,235,0.13),transparent_55%)] p-6 text-slate-100">
        <p role={chatError ? 'alert' : undefined} className={chatError ? 'text-rose-400' : 'text-slate-400'}>
          {chatError || 'Открываем чат…'}
        </p>
        <div className="flex gap-4 text-sm">
          <button type="button" onClick={returnToList} className="text-blue-400 hover:underline">К списку чатов</button>
          {chatError && <button type="button" onClick={retryChat} className="text-blue-400 hover:underline">Повторить</button>}
        </div>
      </main>
    );
  }

  return (
    <main className="scene-enter flex min-h-screen w-full flex-col bg-slate-950 text-slate-100 lg:h-screen lg:flex-row lg:overflow-hidden">
      
      {/* 1. ЛЕВАЯ КОЛОНКА: Чат */}
      <section className="flex h-[65vh] min-h-[460px] w-full flex-col border-b border-slate-800 bg-slate-900/90 lg:h-full lg:min-h-0 lg:w-[32%] lg:min-w-[300px] lg:border-b-0 lg:border-r xl:w-[30%]">
        <header className="border-b border-slate-800 px-4 py-4 sm:px-5">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={returnToList}
              aria-label="К списку чатов"
              title="К списку чатов"
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-slate-700 bg-slate-800/70 text-slate-300 transition-colors hover:border-blue-500/50 hover:bg-blue-500/10 hover:text-blue-300 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-400"
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                <path d="m14.5 5-7 7 7 7" />
              </svg>
            </button>
            <div className="min-w-0 flex-1">
              <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-blue-400">Планировщик путешествий</p>
          {isEditingName ? (
            <form onSubmit={handleRenameChat} className="mt-1 flex min-w-0 items-center gap-2">
              <input
                aria-label="Название чата"
                value={nameDraft}
                onChange={(event) => setNameDraft(event.target.value)}
                maxLength={200}
                disabled={isSavingName}
                className="min-w-0 flex-1 rounded-lg border border-slate-600 bg-slate-800 px-2 py-1 text-sm text-slate-100 outline-none focus:border-blue-500"
              />
              <button type="submit" disabled={!nameDraft.trim() || isSavingName} className="text-xs text-blue-400 hover:text-blue-300 disabled:text-slate-600">Сохранить</button>
              <button type="button" onClick={() => { setIsEditingName(false); setNameError(null); }} className="text-xs text-slate-400 hover:text-white">Отмена</button>
            </form>
          ) : (
            <div className="mt-1 flex min-w-0 items-center gap-2">
              <h1 className="truncate text-base font-semibold tracking-tight text-slate-100" title={data.chat.name}>{data.chat.name}</h1>
              <button type="button" onClick={() => { setNameDraft(data.chat.name); setNameError(null); setIsEditingName(true); }} className="shrink-0 text-xs text-blue-400 hover:text-blue-300 hover:underline">Изменить</button>
            </div>
          )}
            </div>
          </div>
        </header>
        {chatError && <p role="status" className="px-4 py-2 text-xs text-amber-300">{chatError}</p>}
        {nameError && <p role="alert" className="px-4 py-1 text-xs text-rose-400">{nameError}</p>}
        
        <div className="app-scrollbar min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-5 sm:px-5">
          {data.history.map((msg) => (
            <div 
              key={msg.id} 
              className={`flex ${msg.from === 'user' ? 'justify-end' : msg.from === 'agent' ? 'justify-start' : 'justify-center'}`}
            >
              {msg.from !== 'user-action' ? (
                <div className={`max-w-[88%] whitespace-pre-wrap rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm ${
                  msg.from === 'user' 
                    ? 'rounded-tr-md bg-blue-600 text-white shadow-blue-950/30'
                    : 'rounded-tl-md border border-slate-700 bg-slate-800/90 text-slate-200'
                }`}>
                  <MessageText text={msg.message} />
                </div>
              ) : (
                <span className="rounded-full border border-slate-800 bg-slate-950/80 px-3 py-1 text-xs text-slate-500">
                  ⚙️ {msg.message || (msg.operations?.every(op => op.path === '/chat/name') ? 'Изменено название чата' : 'Изменены параметры поездки')}
                </span>
              )}
            </div>
          ))}
        </div>

        <form onSubmit={handleSendMessage} className="border-t border-slate-800 bg-slate-900/95 p-4 sm:p-5">
          {sendError && <p role="alert" className="mb-3 text-xs text-rose-400">{sendError}</p>}
          <div className="flex items-center gap-2 rounded-2xl border border-slate-700 bg-slate-950/70 p-1.5 transition-colors focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-500/15">
            <input
              type="text"
              aria-label="Сообщение агенту"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              disabled={isSending}
              maxLength={10000}
              placeholder="Спросить ИИ-агента…"
              className="min-w-0 flex-1 bg-transparent px-2 py-2 text-sm text-slate-100 outline-none placeholder:text-slate-500"
            />
            <button
              type="submit"
              disabled={!inputValue.trim() || isSending}
              className="flex shrink-0 items-center gap-2 rounded-xl bg-blue-600 px-3 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-blue-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-400 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
            >
              <span>{isSending ? 'Отправка…' : 'Отправить'}</span>
              <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
                <path d="m5 12 14-7-4 14-3-6-7-1Z" />
                <path d="m12 13 7-8" />
              </svg>
            </button>
          </div>
        </form>
      </section>

      <TripEditor data={data} busy={isPatching} onPatch={handlePatch} />
      <Variants data={data} />
    </main>
  );
}
