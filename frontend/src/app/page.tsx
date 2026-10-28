'use client';

import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ChatMessage, ChatSummary, TravelResponse, PatchOperation } from '@/types/travel';
import RegisterForm from '@/components/auth/RegisterForm';
import LoginForm from '@/components/auth/LoginForm';
import ChatListView from '@/components/chat/ChatListView';
import MessageText from '@/components/chat/MessageText';
import AutoTextarea from '@/components/chat/AutoTextarea';
import { useChatSSE } from '../hooks/useChatSSE';
import TripEditor from '@/components/dashboard/TripEditor';
import Variants from '@/components/dashboard/Variants';
import { apiRequest, refreshSession, invalidateSession } from '@/lib/api';
import { userText } from '@/lib/presentation';
import CartDrawer from '@/components/commerce/CartDrawer';
import type { Cart, DemoOrder } from '@/types/commerce';

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

function chatIdFromUrl(): number | null {
  const value = new URLSearchParams(window.location.search).get('chat');
  if (!value) return null;
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

function writeChatUrl(id: number | null, replace = false) {
  const url = new URL(window.location.href);
  if (id === null) url.searchParams.delete('chat');
  else url.searchParams.set('chat', String(id));
  const next = `${url.pathname}${url.search}${url.hash}`;
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (next === current) return;
  if (replace) window.history.replaceState(null, '', next);
  else window.history.pushState(null, '', next);
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
  const [patchError, setPatchError] = useState<string | null>(null);
  const [cart, setCart] = useState<Cart | null>(null);
  const [orders, setOrders] = useState<DemoOrder[]>([]);
  const [cartOpen, setCartOpen] = useState(false);
  const [cartLoading, setCartLoading] = useState(false);
  const [cartBusy, setCartBusy] = useState(false);
  const [cartError, setCartError] = useState<string | null>(null);
  const [latestOrder, setLatestOrder] = useState<DemoOrder | null>(null);
  const [addingVariantId, setAddingVariantId] = useState<number | null>(null);
  const [variantAddError, setVariantAddError] = useState<{ variantId: number; message: string } | null>(null);
  const checkoutKeyRef = useRef<string | null>(null);
  const cartLoadSeq = useRef(0);
  const [showParameters, setShowParameters] = useState(true);
  const [showVariants, setShowVariants] = useState(false);
  const [compactLayout, setCompactLayout] = useState(false);
  const [mobilePanel, setMobilePanel] = useState<'parameters' | 'variants' | null>(null);
  const previousVariantCount = useRef<{ chatId: number; count: number } | null>(null);
  const chatHistoryRef = useRef<HTMLDivElement>(null);
  const lastScrolledChatId = useRef<number | null>(null);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 1279px)');
    const syncLayout = () => {
      setCompactLayout(media.matches);
      setMobilePanel(null);
    };
    syncLayout();
    media.addEventListener('change', syncLayout);
    return () => media.removeEventListener('change', syncLayout);
  }, []);
  const updateToken = useCallback((value: string | null) => {
    setToken(value);
    if (!value) {
      invalidateSession();
      setData(null);
      setSelectedChatId(null);
      setChats([]);
      window.sessionStorage.removeItem('travel-chat');
      writeChatUrl(null, true);
    }
  }, []);

  const loadCart = useCallback(async () => {
    if (!token) return;
    const seq = ++cartLoadSeq.current;
    setCartLoading(true);
    try {
      const [cartResponse, ordersResponse] = await Promise.all([
        apiRequest('/api/v1/cart', token, {}, updateToken),
        apiRequest('/api/v1/orders', token, {}, updateToken),
      ]);
      if (!cartResponse.ok) throw new Error(await responseError(cartResponse, 'Не удалось загрузить корзину'));
      if (!ordersResponse.ok) throw new Error(await responseError(ordersResponse, 'Не удалось загрузить историю оплат'));
      const nextCart = await cartResponse.json() as Cart;
      const nextOrders = await ordersResponse.json() as DemoOrder[];
      if (seq === cartLoadSeq.current) {
        setCart(nextCart);
        setOrders(nextOrders);
        setCartError(null);
      }
    } catch (error) {
      if (seq === cartLoadSeq.current) {
        setCartError(error instanceof Error ? error.message : 'Не удалось загрузить корзину');
      }
    } finally {
      if (seq === cartLoadSeq.current) setCartLoading(false);
    }
  }, [token, updateToken]);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      if (token) void loadCart();
      else {
        cartLoadSeq.current += 1;
        setCart(null);
        setOrders([]);
        setCartOpen(false);
        checkoutKeyRef.current = null;
      }
    }, 0);
    return () => window.clearTimeout(timeout);
  }, [token, loadCart]);

  useEffect(() => {
    let cancelled = false;
    restoreSession()
      .then((accessToken) => {
        if (!cancelled && accessToken) {
          setToken(accessToken);
          const fromUrl = chatIdFromUrl();
          const remembered = Number(window.sessionStorage.getItem('travel-chat'));
          const chatId = fromUrl ?? (Number.isSafeInteger(remembered) && remembered > 0 ? remembered : null);
          if (chatId !== null) {
            setSelectedChatId(chatId);
            window.sessionStorage.setItem('travel-chat', String(chatId));
            if (fromUrl === null) writeChatUrl(chatId, true);
          }
        }
      })
      .finally(() => { if (!cancelled) setAuthReady(true); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const syncFromUrl = () => {
      const chatId = chatIdFromUrl();
      if (chatId === null) window.sessionStorage.removeItem('travel-chat');
      else window.sessionStorage.setItem('travel-chat', String(chatId));
      setSelectedChatId(chatId);
      setData(null);
      setChatError(null);
      setSendError(null);
      setPatchError(null);
      setVariantAddError(null);
      setIsEditingName(false);
      setNameError(null);
      setShowParameters(true);
      setShowVariants(false);
      setMobilePanel(null);
      if (chatId === null) {
        setListLoading(true);
        setListError(null);
      }
    };
    window.addEventListener('popstate', syncFromUrl);
    return () => window.removeEventListener('popstate', syncFromUrl);
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

  const variantsChatId = data?.id ?? null;
  const variantCount = data?.variants.length ?? 0;
  useEffect(() => {
    if (variantsChatId === null) {
      previousVariantCount.current = null;
      return;
    }
    const previous = previousVariantCount.current;
    previousVariantCount.current = { chatId: variantsChatId, count: variantCount };
    if (variantCount === 0 || (previous?.chatId === variantsChatId && previous.count > 0)) return;
    const frame = window.requestAnimationFrame(() => {
      setShowVariants(true);
      if (window.matchMedia('(max-width: 1279px)').matches) setMobilePanel('variants');
    });
    return () => window.cancelAnimationFrame(frame);
  }, [variantsChatId, variantCount]);

  const visibleChatId = data?.id ?? null;
  const messageCount = data?.history.length ?? 0;
  useLayoutEffect(() => {
    const container = chatHistoryRef.current;
    if (!container || visibleChatId === null || visibleChatId !== selectedChatId) {
      lastScrolledChatId.current = null;
      return;
    }
    const scrollToBottom = (behavior: ScrollBehavior) => container.scrollTo({ top: container.scrollHeight, behavior });
    const nearBottom = () => container.scrollHeight - container.clientHeight - container.scrollTop < 80;
    scrollToBottom(lastScrolledChatId.current === visibleChatId ? 'smooth' : 'auto');
    lastScrolledChatId.current = visibleChatId;
    let active = true;
    let fontFrame = 0;
    const frame = window.requestAnimationFrame(() => { if (nearBottom()) scrollToBottom('auto'); });
    void document.fonts.ready.then(() => {
      if (active) fontFrame = window.requestAnimationFrame(() => { if (nearBottom()) scrollToBottom('auto'); });
    });
    return () => {
      active = false;
      window.cancelAnimationFrame(frame);
      window.cancelAnimationFrame(fontFrame);
    };
  }, [visibleChatId, messageCount, selectedChatId]);

  const openChat = (id: number) => {
    window.sessionStorage.setItem('travel-chat', String(id));
    writeChatUrl(id);
    setSelectedChatId(id);
    setData(null);
    setChatError(null);
    setSendError(null);
    setPatchError(null);
    setVariantAddError(null);
    setInputValue('');
    setIsEditingName(false);
    setNameError(null);
    setShowParameters(true);
    setShowVariants(false);
    setMobilePanel(null);
  };

  const returnToList = () => {
    window.sessionStorage.removeItem('travel-chat');
    writeChatUrl(null);
    setSelectedChatId(null);
    setData(null);
    setChatError(null);
    setSendError(null);
    setPatchError(null);
    setVariantAddError(null);
    setIsEditingName(false);
    setNameError(null);
    setMobilePanel(null);
    setListLoading(true);
    setListError(null);
  };

  const retryList = () => {
    setListLoading(true);
    setListError(null);
    setListReloadKey((key) => key + 1);
  };

  const deleteChat = async (id: number) => {
    if (!token) throw new Error('Сессия завершилась. Войдите снова.');
    const response = await apiRequest(`/api/v1/chats/${id}`, token, { method: 'DELETE' }, updateToken);
    if (!response.ok) throw new Error(await responseError(response, 'Не удалось удалить чат'));
    setChats(current => current.filter(chat => chat.id !== id));
  };

  const retryChat = () => {
    setChatError(null);
    setChatReloadKey((key) => key + 1);
  };

  const togglePanel = (panel: 'parameters' | 'variants') => {
    if (compactLayout) {
      setMobilePanel(current => current === panel ? null : panel);
      return;
    }
    if (panel === 'parameters') setShowParameters(current => !current);
    else setShowVariants(current => !current);
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
      const saved = await response.json() as ChatMessage;
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
    if (!token || !data || isPatching) return false;
    const chatId = data.id;
    setIsPatching(true);
    setPatchError(null);
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
      const saved: ChatMessage & { revision: number } = await response.json();
      const latest = await apiRequest(`/api/v1/chats/${chatId}`, token, {}, updateToken);
      if (!latest.ok) {
        throw new Error('Изменение сохранено, но не удалось обновить экран. Обновите страницу.');
      }
      const snapshot: TravelResponse = await latest.json();
      setData(current => {
        if (!current || current.id !== chatId || snapshot.revision < current.revision) return current;
        const history = snapshot.history.some(item => item.id === saved.id)
          ? snapshot.history : [...snapshot.history, saved];
        return { ...snapshot, history };
      });
      return true;
    } catch (error) {
      setPatchError(error instanceof Error ? error.message : 'Не удалось сохранить параметры');
      return false;
    } finally { setIsPatching(false); }
  };

  const openCart = () => {
    setCartOpen(true);
    setLatestOrder(null);
    void loadCart();
  };

  const closeCart = useCallback(() => setCartOpen(false), []);

  const addVariantToCart = async (variantId: number) => {
    if (!token || !data || addingVariantId !== null) return;
    setAddingVariantId(variantId);
    setVariantAddError(null);
    try {
      const response = await apiRequest('/api/v1/cart/items', token, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: data.id, variant_id: variantId }),
      }, updateToken);
      if (!response.ok) throw new Error(await responseError(response, 'Не удалось добавить вариант'));
      await loadCart();
      setCartOpen(true);
    } catch (error) {
      setVariantAddError({ variantId, message: error instanceof Error ? error.message : 'Не удалось добавить вариант' });
    } finally {
      setAddingVariantId(null);
    }
  };

  const changeCart = async (path: string, init: RequestInit) => {
    if (!token || cartBusy) return;
    setCartBusy(true);
    setCartError(null);
    checkoutKeyRef.current = null;
    try {
      const response = await apiRequest(path, token, init, updateToken);
      if (!response.ok) throw new Error(await responseError(response, 'Не удалось изменить корзину'));
      await loadCart();
    } catch (error) {
      setCartError(error instanceof Error ? error.message : 'Не удалось изменить корзину');
    } finally {
      setCartBusy(false);
    }
  };

  const checkout = async () => {
    if (!token || cartBusy || !cart?.selected_count) return;
    setCartBusy(true);
    setCartError(null);
    checkoutKeyRef.current ??= crypto.randomUUID();
    try {
      const response = await apiRequest('/api/v1/cart/checkout', token, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ idempotency_key: checkoutKeyRef.current }),
      }, updateToken);
      if (!response.ok) {
        if (response.status === 409) checkoutKeyRef.current = null;
        if (response.status === 409) await loadCart();
        throw new Error(await responseError(response, 'Не удалось выполнить демо-оплату'));
      }
      const order = await response.json() as DemoOrder;
      setLatestOrder(order);
      checkoutKeyRef.current = null;
      await loadCart();
    } catch (error) {
      setCartError(error instanceof Error ? error.message : 'Не удалось выполнить демо-оплату');
    } finally {
      setCartBusy(false);
    }
  };

  const cartDrawer = cartOpen && <CartDrawer cart={cart} orders={orders} error={cartError}
    busy={cartBusy} loading={cartLoading} latestOrder={latestOrder} onClose={closeCart}
    onRetry={() => void loadCart()}
    onRemove={itemId => void changeCart(`/api/v1/cart/items/${itemId}`, { method: 'DELETE' })}
    onRefresh={itemId => void changeCart(`/api/v1/cart/items/${itemId}/refresh`, { method: 'POST' })}
    onSelect={(itemId, lineId, selected) => void changeCart(
      `/api/v1/cart/items/${itemId}/lines/${encodeURIComponent(lineId)}`,
      { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ selected }) })}
    onCheckout={() => void checkout()} />;

  if (!authReady) {
    return <main className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-400">Загружаем приложение…</main>;
  }

  if (!token) {
    return (
      <main className="flex h-screen w-screen items-center justify-center bg-slate-950 p-4 text-slate-100">
        {authScreen === 'login' ? (
          <LoginForm onSuccess={(accessToken) => { invalidateSession(); setListLoading(true); setListError(null); setToken(accessToken); setSelectedChatId(chatIdFromUrl()); }} onSwitchToRegister={() => setAuthScreen('register')} />
        ) : (
          <RegisterForm onSuccess={() => setAuthScreen('login')} onSwitchToLogin={() => setAuthScreen('login')} />
        )}
      </main>
    );
  }

  if (selectedChatId === null) {
    return (
      <><ChatListView
        chats={chats}
        isLoading={listLoading}
        listError={listError}
        createError={createError}
        isCreating={isCreating}
        draft={newChatDraft}
        onDraftChange={setNewChatDraft}
        onCreate={handleCreateChat}
        onOpenChat={openChat}
        onDeleteChat={deleteChat}
        onRetry={retryList}
        onLogout={handleLogout}
        onOpenCart={openCart}
        cartCount={cart?.items.length ?? 0}
      />{cartDrawer}</>
    );
  }

  if (!data || data.id !== selectedChatId) {
    return (
      <main className="scene-enter flex min-h-screen flex-col items-center justify-center gap-5 bg-slate-950 bg-[radial-gradient(ellipse_at_50%_35%,rgba(0,56,246,0.10),transparent_55%)] p-6 text-slate-100">
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

  const agentStatusText = userText(data.agent['status-message']) || ({
    idle: 'Начинаем подбор',
    working: 'Подбираю варианты',
    waiting_user: 'Нужно одно уточнение',
    completed: 'Подбор завершён',
    error: 'Не удалось обновить подбор',
  }[data.agent.status]);

  return (
    <main className="scene-enter relative flex h-dvh min-h-0 w-full overflow-hidden bg-slate-950 text-slate-100">
      
      {/* 1. ЛЕВАЯ КОЛОНКА: Чат */}
      <section className="flex h-full min-h-0 min-w-0 flex-1 flex-col border-r border-slate-800 bg-slate-900/90">
        <header className="chat-panel-header flex shrink-0 flex-wrap items-center gap-2 px-4 py-3">
            <button
              type="button"
              onClick={returnToList}
              aria-label="К списку чатов"
              title="К списку чатов"
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-white/80 bg-white/75 text-[var(--color-brand-navy)] transition-colors hover:bg-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-brand-navy)]"
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                <path d="m14.5 5-7 7 7 7" />
              </svg>
            </button>
            <div className="flex min-w-[150px] flex-1 items-center gap-2">
              {isEditingName ? (
                <form onSubmit={handleRenameChat} className="panel-heading-label panel-heading-label--chat min-w-0 flex-1 gap-2">
                  <input
                    aria-label="Название чата"
                    value={nameDraft}
                    onChange={(event) => setNameDraft(event.target.value)}
                    maxLength={200}
                    disabled={isSavingName}
                    className="min-w-0 flex-1 bg-transparent text-sm font-semibold text-[var(--color-brand-navy)] outline-none placeholder:text-[var(--color-brand-navy)]/60 focus-visible:underline disabled:opacity-60"
                  />
                  <button type="submit" disabled={!nameDraft.trim() || isSavingName} aria-label="Сохранить название" title="Сохранить название" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--color-primary)] text-white transition-colors hover:bg-[var(--color-primary-hover)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-focus)] disabled:cursor-not-allowed disabled:opacity-40">
                    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5"><path d="m5 12 4.5 4.5L19 7" /></svg>
                  </button>
                  <button type="button" disabled={isSavingName} onClick={() => { setIsEditingName(false); setNameError(null); }} aria-label="Отменить переименование" title="Отменить переименование" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white/75 text-[var(--color-brand-navy)] transition-colors hover:bg-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-brand-navy)] disabled:cursor-not-allowed disabled:opacity-40">
                    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="h-5 w-5"><path d="M6 6 18 18M18 6 6 18" /></svg>
                  </button>
                </form>
              ) : (
                <>
                  <h1 className="panel-heading-label panel-heading-label--chat min-w-0 flex-1" title={data.chat.name}>
                    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5 shrink-0">
                      <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v9a2.5 2.5 0 0 1-2.5 2.5H9l-5 4V5.5Z" />
                    </svg>
                    <span className="truncate">{data.chat.name}</span>
                  </h1>
                  <button type="button" onClick={() => { setNameDraft(data.chat.name); setNameError(null); setIsEditingName(true); }} aria-label="Изменить название чата" title="Изменить название чата" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl text-[var(--color-brand-navy)] transition-colors hover:bg-white/70">
                    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5"><path d="m15.5 5.5 3 3M4 20l4.5-1 10-10a2.12 2.12 0 0 0-3-3l-10 10L4 20Z" /></svg>
                  </button>
                </>
              )}
            </div>
            <div className="ml-auto flex w-full flex-row items-center justify-end gap-2 sm:w-auto sm:flex-col sm:items-stretch sm:gap-1.5">
              <button type="button" onClick={() => togglePanel('parameters')} aria-controls="trip-parameters-panel" aria-expanded={compactLayout ? mobilePanel === 'parameters' : showParameters} className="panel-toggle-button">
                <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 shrink-0"><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" /><circle cx="12" cy="12" r="2.5" /></svg>
                <span>Параметры поездки</span>
              </button>
              <button type="button" onClick={() => togglePanel('variants')} aria-controls="trip-variants-panel" aria-expanded={compactLayout ? mobilePanel === 'variants' : showVariants} className="panel-toggle-button">
                <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 shrink-0"><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6-9.5-6Z" /><circle cx="12" cy="12" r="2.5" /></svg>
                <span>Варианты поездки</span>
              </button>
            </div>
        </header>
        {chatError && <p role="status" className="px-4 py-2 text-xs text-amber-300">{chatError}</p>}
        {nameError && <p role="alert" className="px-4 py-1 text-xs text-rose-400">{nameError}</p>}
        
        <div className="relative min-h-0 flex-1">
          <div ref={chatHistoryRef} className="app-scrollbar h-full space-y-4 overflow-y-auto px-4 pb-5 pt-16 sm:px-5">
          {data.history.map((msg) => (
            <div 
              key={msg.id} 
              className={`flex ${msg.from === 'user' ? 'justify-end' : msg.from === 'agent' ? 'justify-start' : 'justify-center'}`}
            >
              {msg.from !== 'user-action' ? (
                <div className={`max-w-[88%] whitespace-pre-wrap rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm ${
                  msg.from === 'user' 
                    ? 'rounded-tr-md bg-user-message text-user-black shadow-blue-950/30'
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
          <p role="status" className="agent-status-badge pointer-events-none absolute right-4 top-3 z-10 max-w-[min(82%,340px)] rounded-xl px-3 py-2 text-right text-xs font-medium leading-snug sm:right-5 sm:text-sm">
            {agentStatusText}
          </p>
        </div>

        <form onSubmit={handleSendMessage} className="border-t border-slate-800 bg-slate-900/95 p-4 sm:p-5">
          {sendError && <p role="alert" className="mb-3 text-xs text-rose-400">{sendError}</p>}
          <div className="flex items-end gap-2 rounded-2xl border border-slate-700 bg-slate-950/70 p-1.5 transition-colors focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-500/15">
            <AutoTextarea value={inputValue} onChange={setInputValue} disabled={isSending}
              className="min-w-0 flex-1 bg-transparent px-2 py-2 text-sm leading-6 text-slate-100 outline-none placeholder:text-slate-500" />
            <button
              type="submit"
              disabled={!inputValue.trim() || isSending}
              className="flex shrink-0 items-center gap-2 rounded-xl bg-[var(--color-primary)] px-3 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-[var(--color-primary-hover)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-focus)] disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
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

      <button type="button" aria-label="Вернуться к чату" aria-hidden={mobilePanel === null} tabIndex={mobilePanel === null ? -1 : 0} onClick={() => setMobilePanel(null)} className={`chat-backdrop ${mobilePanel ? 'chat-backdrop--visible' : ''}`} />
      <div className={`panel-shell ${mobilePanel === 'parameters' ? 'block' : 'hidden'} xl:block xl:h-full ${showParameters ? 'panel-shell--desktop-open' : ''}`}>
        <TripEditor data={data} busy={isPatching} error={patchError} onPatch={handlePatch} onClose={() => setMobilePanel(null)} />
      </div>
      <div className={`panel-shell ${mobilePanel === 'variants' ? 'block' : 'hidden'} xl:block xl:h-full ${showVariants ? 'panel-shell--desktop-open' : ''}`}>
        <Variants data={data} onClose={() => setMobilePanel(null)} onAdd={addVariantToCart}
          onOpenCart={openCart} addingVariantId={addingVariantId} addError={variantAddError}
          cartCount={cart?.items.length ?? 0}
          cartVariantIds={new Set(cart?.items.filter(item => item.chat_id === data.id).map(item => item.variant_id) ?? [])} />
      </div>
      {cartDrawer}
    </main>
  );
}
