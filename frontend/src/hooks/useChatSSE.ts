import { useEffect } from 'react';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import { TravelResponse } from '@/types/travel';

interface UseChatSSEProps {
  chatId: number;
  token: string | null;
  onInitInfo: (data: TravelResponse) => void;
  onNewMessage: (message: any) => void;
  onChangedTravel: (travelData: { revision: number; basic: any; variants: any; additional: any }) => void;
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

    // ЗАЩИТА: Если мы в Mock-режиме, не шлем запросы в сеть, чтобы избежать ошибок Content-Type
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
            console.error('SSE Error:', err);
            throw err; 
          }
        });
      } catch (error) {
        console.error('SSE connection failed:', error);
      }
    }

    startListening();

    return () => {
      abortController.abort();
    };
  }, [chatId, token]);
}
