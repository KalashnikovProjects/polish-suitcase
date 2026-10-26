'use client';

import React, { useEffect, useState } from 'react';
import type { ChatMessage, ChatSummary, TravelResponse } from '@/types/travel';
import RegisterForm from '@/components/auth/RegisterForm';
import LoginForm from '@/components/auth/LoginForm';
import ChatListView from '@/components/chat/ChatListView';
import { useChatSSE } from '../hooks/useChatSSE';
import PeoplesCounter from '@/components/dashboard/PeoplesCounter';
import LocationTree from '@/components/dashboard/LocationTree';

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

let pendingSessionRestore: Promise<string | null> | null = null;

function restoreSession(): Promise<string | null> {
  if (!pendingSessionRestore) {
    pendingSessionRestore = fetch('/api/v1/auth/refresh', { method: 'POST', credentials: 'same-origin' })
      .then(async (response) => response.ok ? (await response.json()).access_token as string : null)
      .catch(() => null)
      .finally(() => { pendingSessionRestore = null; });
  }
  return pendingSessionRestore;
}

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
  const [peopleError, setPeopleError] = useState<string | null>(null);
  const [isSavingPeople, setIsSavingPeople] = useState(false);
  const [newChatDraft, setNewChatDraft] = useState('');
  const [inputValue, setInputValue] = useState('');
  const [listReloadKey, setListReloadKey] = useState(0);
  const [chatReloadKey, setChatReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    restoreSession()
      .then((accessToken) => { if (!cancelled && accessToken) setToken(accessToken); })
      .finally(() => { if (!cancelled) setAuthReady(true); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!token) return;
    const timer = window.setInterval(() => {
      fetch('/api/v1/auth/refresh', { method: 'POST', credentials: 'same-origin' })
        .then(async (response) => {
          if (response.ok) {
            const body = await response.json();
            setToken(body.access_token);
          } else if (response.status === 401) {
            setToken(null);
            setSelectedChatId(null);
            setData(null);
          }
        })
        .catch(() => undefined);
    }, 25 * 60 * 1000);
    return () => window.clearInterval(timer);
  }, [token]);

  useEffect(() => {
    if (!token || selectedChatId !== null) return;
    let cancelled = false;
    fetch('/api/v1/chats?limit=100', { headers: { Authorization: `Bearer ${token}` } })
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseError(response, 'Не удалось загрузить чаты'));
        return response.json() as Promise<ChatSummary[]>;
      })
      .then((items) => { if (!cancelled) setChats(items); })
      .catch((error) => { if (!cancelled) setListError(error.message); })
      .finally(() => { if (!cancelled) setListLoading(false); });
    return () => { cancelled = true; };
  }, [token, selectedChatId, listReloadKey]);

  useEffect(() => {
    if (!token || selectedChatId === null) return;
    let cancelled = false;
    fetch(`/api/v1/chats/${selectedChatId}`, { headers: { Authorization: `Bearer ${token}` } })
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseError(response, 'Не удалось открыть чат'));
        return response.json() as Promise<TravelResponse>;
      })
      .then((chat) => { if (!cancelled) setData((current) => current?.id === chat.id ? current : chat); })
      .catch((error) => { if (!cancelled) setChatError(error.message); });
    return () => { cancelled = true; };
  }, [token, selectedChatId, chatReloadKey]);

  useChatSSE({
    chatId: selectedChatId ?? 0,
    token: selectedChatId === null ? null : token,
    onInitInfo: (chat) => setData((current) =>
      current?.id === chat.id && current.revision > chat.revision ? current : chat),
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
    setSelectedChatId(id);
    setData(null);
    setChatError(null);
    setSendError(null);
    setInputValue('');
    setIsEditingName(false);
    setNameError(null);
    setPeopleError(null);
  };

  const returnToList = () => {
    setSelectedChatId(null);
    setData(null);
    setChatError(null);
    setSendError(null);
    setIsEditingName(false);
    setNameError(null);
    setPeopleError(null);
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
      const response = await fetch('/api/v1/chats', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: chatNameFromMessage(message), message }),
      });
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
      const response = await fetch(`/api/v1/chats/${data.id}/messages`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ message }),
      });
      if (!response.ok) throw new Error(await responseError(response, 'Не удалось отправить сообщение'));
      const saved: ChatMessage & { revision: number } = await response.json();
      setData((current) => current && current.id === data.id && !current.history.some((item) => item.id === saved.id)
        ? { ...current, history: [...current.history, saved] }
        : current);
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
      const response = await fetch(`/api/v1/chats/${data.id}/messages`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          from_revision: data.revision,
          operations: [{ op: 'replace', path: '/chat/name', value: name }],
        }),
      });
      if (!response.ok) throw new Error(await responseError(response, 'Не удалось переименовать чат'));
      const saved: { revision: number } = await response.json();
      setData((current) => current && current.id === data.id && current.revision <= saved.revision
        ? { ...current, revision: saved.revision, chat: { name } }
        : current);
      setIsEditingName(false);
    } catch (error) {
      setNameError(error instanceof Error ? error.message : 'Не удалось переименовать чат');
    } finally {
      setIsSavingName(false);
    }
  };

  const savePeoplesValue = async (field: 'adults' | 'children' | 'additional-info', value: number | string[]) => {
    if (!token || !data || isSavingPeople || data.basic.peoples[field] === value) return;
    const chatId = data.id;
    setIsSavingPeople(true);
    setPeopleError(null);
    try {
      const response = await fetch(`/api/v1/chats/${chatId}/messages`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          from_revision: data.revision,
          operations: [{ op: 'replace', path: `/basic/peoples/${field}`, value }],
        }),
      });
      if (response.status === 409) {
        const currentResponse = await fetch(`/api/v1/chats/${chatId}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (currentResponse.ok) {
          const latest: TravelResponse = await currentResponse.json();
          setData((current) => current?.id === chatId && current.revision > latest.revision ? current : latest);
        }
        throw new Error('Параметры поездки изменились. Проверьте значения и повторите действие.');
      }
      if (!response.ok) throw new Error(await responseError(response, 'Не удалось сохранить состав группы'));
      const saved: ChatMessage & { revision: number } = await response.json();
      setData((current) => {
        if (!current || current.id !== chatId) return current;
        const updated = saved.revision > current.revision
          ? { ...current, revision: saved.revision, basic: {
              ...current.basic,
              peoples: { ...current.basic.peoples, [field]: value },
            } }
          : current;
        return updated.history.some((item) => item.id === saved.id)
          ? updated
          : { ...updated, history: [...updated.history, saved] };
      });
    } catch (error) {
      setPeopleError(error instanceof Error ? error.message : 'Не удалось сохранить состав группы');
    } finally {
      setIsSavingPeople(false);
    }
  };

  const handleLogout = async () => {
    try {
      const response = await fetch('/api/v1/auth/logout', { method: 'POST', credentials: 'same-origin' });
      if (!response.ok) throw new Error('Не удалось выйти из аккаунта');
      setToken(null);
      setChats([]);
      returnToList();
    } catch (error) {
      setListError(error instanceof Error ? error.message : 'Не удалось выйти из аккаунта');
    }
  };

  if (!authReady) {
    return <main className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-400">Загружаем приложение…</main>;
  }

  if (!token) {
    return (
      <main className="flex h-screen w-screen items-center justify-center bg-slate-950 p-4 text-slate-100">
        {authScreen === 'login' ? (
          <LoginForm onSuccess={(accessToken) => { setListLoading(true); setListError(null); setToken(accessToken); }} onSwitchToRegister={() => setAuthScreen('register')} />
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
                  {msg.message}
                </div>
              ) : (
                <span className="rounded-full border border-slate-800 bg-slate-950/80 px-3 py-1 text-xs text-slate-500">
                  ⚙️ {msg.message}
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

      {/* 2. ЦЕНТРАЛЬНАЯ КОЛОНКА: Конструктор параметров (Basic) */}
      <section className="flex min-h-[420px] w-full flex-col border-b border-slate-800 bg-slate-950 bg-[radial-gradient(ellipse_at_50%_10%,rgba(37,99,235,0.09),transparent_60%)] lg:h-full lg:min-h-0 lg:w-[38%] lg:border-b-0 lg:border-r xl:w-[40%]">
        <div className="flex min-h-[73px] items-center border-b border-slate-800 px-5 py-4 text-xs font-semibold uppercase tracking-[0.16em] text-slate-300">
          <span>Параметры поездки</span>
        </div>
        
        {/* Контейнер со скроллом, где блоки идут друг за другом */}
        <div className="app-scrollbar min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
          
          {/* Блок 1: Отправление */}
          <div className="rounded-2xl border border-slate-800 bg-slate-900/90 p-5 shadow-lg shadow-black/10">
            <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-1">Точка отправления</h3>
            <p className="text-sm font-medium text-slate-200">{data.basic.departure}</p>
          </div>

          {/* Блок 2: Состав группы (PeoplesCounter) */}
            <PeoplesCounter 
              peoples={data.basic.peoples}
              isSaving={isSavingPeople}
              onRemoveTag={(tagValue) => savePeoplesValue('additional-info',
                data.basic.peoples['additional-info'].filter((tag) => tag !== tagValue))}
              onUpdateCount={(type, newValue) => savePeoplesValue(type, newValue)}
            />
            {peopleError && <p role="alert" className="text-sm text-rose-400">{peopleError}</p>}
          

          {/* Блок 3: Бюджет */}
          <div className="rounded-2xl border border-slate-800 bg-slate-900/90 p-5 shadow-lg shadow-black/10">
            <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-1">Оценка бюджета (ИИ)</h3>
            <p className="text-base font-semibold text-emerald-400">{data.basic.cost_range}</p>
          </div>

          {/* ВОТ ЗДЕСЬ (В САМОМ КОНЦЕ) НАХОДИТСЯ БЛОК ДЕРЕВА ЛОКАЦИЙ! */}
          <div className="space-y-3 rounded-2xl border border-slate-800 bg-slate-900/90 p-5 shadow-lg shadow-black/10">
            <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wider">Рассматриваемые локации</h3>
            <div className="pt-1">
              <LocationTree 
                tree={data.basic.locations_tree}
                onRemovePath={(fullPath) => {
                  const newLogMessage = {
                    id: Date.now(),
                    from: 'user-action' as const,
                    message: `Пользователь исключил из маршрута: ${fullPath.split('/').pop()}`
                  };
                  setData(prev => prev?.id === data.id ? ({
                    ...prev,
                    history: [...prev.history, newLogMessage]
                  }) : prev);
                  setTimeout(() => {
                    const agentNotice = {
                      id: Date.now() + 1,
                      from: 'agent' as const,
                      message: `Принято. Я исключил "${fullPath.split('/').pop()}" из планов и перестраиваю варианты отелей.`
                    };
                    setData(prev => prev?.id === data.id ? ({
                      ...prev,
                      history: [...prev.history, agentNotice]
                    }) : prev);
                  }, 1000);
                }}
              />
            </div>
          </div>

        </div>
      </section>


      {/* 3. ПРАВАЯ КОЛОНКА: Варианты от ИИ */}
      <section className="flex min-h-[350px] w-full flex-col bg-slate-900/60 lg:h-full lg:min-h-0 lg:w-[30%]">
        <div className="flex min-h-[73px] items-center justify-between gap-3 border-b border-slate-800 px-5 py-4 text-xs font-semibold uppercase tracking-[0.16em] text-slate-300">
          <span>Варианты от ИИ</span>
          {data.agent['status-message'] && (
            <span className="rounded-lg border border-amber-800/50 bg-amber-950/50 px-2.5 py-1 text-[11px] font-medium normal-case tracking-normal text-amber-400">
              {data.agent['status-message']}
            </span>
          )}
        </div>
        
        <div className="app-scrollbar min-h-0 flex-1 space-y-4 overflow-y-auto p-4 sm:p-5">
          {data.variants.map((v) => (
            <div key={v.id} className="space-y-3 rounded-2xl border border-slate-800 bg-slate-900/90 p-5 shadow-lg shadow-black/10">
              <div className="flex justify-between items-start">
                <div>
                  <h4 className="font-semibold text-sm text-slate-200">{v.name}</h4>
                  <p className="text-xs text-slate-500">{v.location}</p>
                </div>
                <span className="text-xs bg-emerald-950 text-emerald-400 px-2 py-1 rounded font-bold">
                  {v.price}
                </span>
              </div>
              <p className="text-xs text-slate-400">{v.description}</p>
              
              {v.transports.map((t) => (
                <div key={t.id} className="p-2 bg-slate-950 rounded border border-slate-800 flex items-center justify-between text-xs">
                  <div className="flex items-center gap-2">
                    <span>{t.type === 'flight' ? '✈️' : '🚌'}</span>
                    <span className="text-slate-300 font-medium">{t.name}</span>
                  </div>
                  <span className="text-emerald-500 font-semibold">{t.price}</span>
                </div>
              ))}
              
              {v.habitations?.map((h) => (
                <div key={h.id} className="p-2 bg-slate-950 rounded border border-slate-800 text-xs space-y-1">
                  <div className="flex justify-between items-center font-medium text-slate-300">
                    <span>🏨 {h.name}</span>
                    <span>🏨 {h.name}</span>
                    <span className="text-emerald-500">{h.price}</span>
                  </div>
                  {h.additional?.map((attr, idx) => (
                    <p key={idx} className="text-[11px] text-slate-500">
                      • {attr.label}: <span className="text-slate-400">{attr.value}</span>
                    </p>
                  ))}
                </div>
              ))}
            </div>
          ))}
        </div>
      </section>

    </main>
  );
}
