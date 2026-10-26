import { useEffect } from 'react';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import { TravelResponse } from '@/types/travel';

interface UseChatSSEProps {
  chatId: number;
  token: string | null;
  onInitInfo: (data: TravelResponse) => void;
  onNewMessage: (message: any) => void;
  onChangedTravel: (travelData: Pick<TravelResponse, 'revision' | 'chat' | 'basic' | 'variants' | 'additional'>) => void;
  onAgentInfo: (agentData: { status: string; 'status-message': string }) => void;
}

export function useChatSSE({
  chatId,
  token,
  onInitInfo,
  onNewMessage,
  onChangedTravel,
  onAgentInfo
}: UseChatSSEProps) {
  useEffect(() => {
    if (!token || !chatId) return;

    // ЗАЩИТА: Полностью исключаем mock-токен из боевой сборки
    if (token === 'mock_jwt_access_token_for_testing') {
      console.log('SSE поток запущен в автономном Mock-режиме 🚀');
      return;
    }

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
          
          onmessage(event) {
            if (!event.data) return;
            const parsedData = JSON.parse(event.data);

            switch (event.event) {
              case 'init_info':
                onInitInfo(parsedData);
                break;
              case 'new_message':
                onNewMessage(parsedData);
                break;
              case 'changed_travel':
                onChangedTravel(parsedData);
                break;
              case 'agent_info':
                onAgentInfo(parsedData);
                break;
            }
          },
          onerror(err) {
            console.error('SSE connection broken, attempting auto-reconnect...', err);
            // Важно: НЕ пишем throw err. Возврат пустой строки или ничего 
            // сообщает fetch-event-source, что нужно выполнить reconnect.
          }
        });
      } catch (error) {
        console.error('SSE fatal initialization failure:', error);
      }
    }

    startListening();

    return () => {
      abortController.abort();
    };
  }, [chatId, token]);
}
