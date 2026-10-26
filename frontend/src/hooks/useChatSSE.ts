import { useEffect, useRef } from 'react';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import type { TravelResponse, ChatMessage } from '@/types/travel';

interface UseChatSSEProps {
  chatId: number;
  token: string | null;
  onInitInfo: (data: TravelResponse) => void;
  onNewMessage: (message: ChatMessage) => void;
  onChangedTravel: (travelData: Pick<TravelResponse, 'revision' | 'chat' | 'basic' | 'variants' | 'additional'>) => void;
  onAgentInfo: (agentData: TravelResponse['agent']) => void;
  onError?: (message: string | null) => void;
  onUnauthorized?: (signal: AbortSignal) => Promise<void>;
}

export function useChatSSE({
  chatId,
  token,
  onInitInfo,
  onNewMessage,
  onChangedTravel,
  onAgentInfo,
  onError,
  onUnauthorized
}: UseChatSSEProps) {
  const callbacks = useRef({ onInitInfo, onNewMessage, onChangedTravel, onAgentInfo, onError, onUnauthorized });
  useEffect(() => { callbacks.current = { onInitInfo, onNewMessage, onChangedTravel, onAgentInfo, onError, onUnauthorized }; }, [onInitInfo, onNewMessage, onChangedTravel, onAgentInfo, onError, onUnauthorized]);
  useEffect(() => {
    if (!token || !chatId) return;

    const abortController = new AbortController();

    async function startListening() {
      try {
        await fetchEventSource(`/api/v1/chats/${chatId}/events`, {
          method: 'GET',
          headers: {
            'Authorization': `Bearer ${token}`,
            'Accept': 'text/event-stream',
          },
          signal: abortController.signal,
          async onopen(response) {
            if (response.status === 401) {
              await callbacks.current.onUnauthorized?.(abortController.signal);
              abortController.abort();
              return;
            }
            if (response.status === 404) {
              callbacks.current.onError?.('Чат удалён или недоступен');
              abortController.abort();
              return;
            }
            if (!response.ok || !response.headers.get('content-type')?.startsWith('text/event-stream')) throw new Error('Нет соединения с сервером');
            callbacks.current.onError?.(null);
          },
          
          onmessage(event) {
            if (!event.data || abortController.signal.aborted) return;
            const parsedData = JSON.parse(event.data);

            switch (event.event) {
              case 'init_info':
                if (parsedData.id !== chatId || !Array.isArray(parsedData.history)) throw new Error('Некорректное состояние чата');
                callbacks.current.onInitInfo(parsedData);
                break;
              case 'new_message':
                callbacks.current.onNewMessage(parsedData);
                break;
              case 'changed_travel':
                callbacks.current.onChangedTravel(parsedData);
                break;
              case 'agent_info':
                callbacks.current.onAgentInfo(parsedData);
                break;
            }
          },
          onclose() { if (!abortController.signal.aborted) throw new Error('Соединение прервано'); },
          onerror(err) {
            if (abortController.signal.aborted) throw err;
            callbacks.current.onError?.('Восстанавливаем соединение…');
            return 2000;
          },
        });
      } catch (error) {
        if (!abortController.signal.aborted) callbacks.current.onError?.(error instanceof Error ? error.message : 'Ошибка соединения');
      }
    }

    startListening();

    return () => {
      abortController.abort();
    };
  }, [chatId, token]);
}
