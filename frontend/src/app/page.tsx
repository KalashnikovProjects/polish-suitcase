'use client';

import React, { useState } from 'react';
import { TravelResponse } from '@/types/travel';
import RegisterForm from '@/components/auth/RegisterForm';
import LoginForm from '@/components/auth/LoginForm';
import { useChatSSE } from '../hooks/useChatSSE';
import PeoplesCounter from '@/components/dashboard/PeoplesCounter';
import LocationTree from '@/components/dashboard/LocationTree';


const mockData: TravelResponse = {
  id: 1,
  revision: 19,
  chat: {
    name: "Путешествия на ноябрь",
    history: [
      { id: 1234, from: "user", message: "Хочу в Уфу в ноябре" },
      { id: 1235, from: "user-action", message: "Пользователь изменил дату на 29.10.2026" },
      { id: 1236, from: "agent", message: "Обоснование решения: Уфа — отличный выбор для культурного отдыха." }
    ]
  },
  basic: {
    departure: "Россия, Москва, МИРЭА",
    dates: { from: "2026-10-29", to: "-" },
    peoples: { adults: 1, children: 0, unknown: 1, "additional-info": ["с собакой"] },
    price_restrictions: { min: -1, max: -1 },
    cost_range: "35000–42000 ₽",
    locations_tree: { "Россия": { "Урал": { "Башкирия": { "Уфа": {} } } } }
  },
  variants: [
    {
      id: 12345,
      name: "Чудесная поездка в Уфу",
      location: "Россия, Урал, Уфа",
      price: "10к-20к",
      dates: { from: "1 ноября", to: "2 ноября" },
      completed: true,
      description: "Увлекательные выходные в столице Башкирии.",
      urls: ["https://example.com"],
      habitations: [
        {
          id: 38752501,
          name: "Общежитие ПТУ №3",
          description: "Бюджетное размещение.",
          dates: { from: "-", to: "-" },
          url: "...",
          price: "42 рублей",
          additional: [{ label: "Тип комнаты", value: "Семейная" }]
        }
      ],
      transports: [
        {
          id: 222222,
          name: "Перелёт из Москвы в Астану",
          type: "flight",
          price: "123 рубля",
          additional: [{ label: "Ограничения", value: "Багаж до 5кг" }]
        }
      ],
      additional: [{ label: "Время в пути", value: "10 часов" }]
    }
  ],
  additional: [],
  agent: { status: "working", "status-message": "Поиск отелей..." }
};

export default function Home() {
  const [inputValue, setInputValue] = useState('');
  const [token, setToken] = useState<string | null>(null);
  const [authScreen, setAuthScreen] = useState<'login' | 'register'>('login');
  const [data, setData] = useState<TravelResponse>(mockData);
  
  // Функция для отправки сообщения
  const handleSendMessage = async (e: React.FormEvent) => {
  e.preventDefault();
  if (!inputValue.trim() || !token) return;

  const currentInput = inputValue;
  setInputValue('');

  try {
    const response = await fetch(`/api/v1/chats/${data.id}/messages`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ message: currentInput })
    });

    if (!response.ok) throw new Error('Не удалось отправить сообщение');
    
    // ВАЖНО: Локально добавлять сообщение агента через setTimeout больше НЕ НАДО.
    // Новый ответ от ИИ прилетит автоматически через SSE (useChatSSE -> onNewMessage)
  } catch (err) {
    console.error(err);
    // Вернуть текст в инпут, если не отправилось
    setInputValue(currentInput); 
  }
};


  useChatSSE({
    chatId: data.id,
    token: token,
    onInitInfo: (initData) => {
      setData(initData);
    },
    onNewMessage: (newMessage) => {
      setData((prev) => ({
        ...prev,
        chat: {
          ...prev.chat,
          history: [...prev.chat.history, newMessage]
        }
      }));
    },
    onChangedTravel: (travelUpdate) => {
      setData((prev) => {
        if (travelUpdate.revision > prev.revision) {
          return {
            ...prev,
            revision: travelUpdate.revision,
            basic: travelUpdate.basic,
            variants: travelUpdate.variants,
            additional: travelUpdate.additional
          };
        }
        return prev;
      });
    },
    onAgentInfo: (agentData) => {
      setData((prev) => ({
        ...prev,
        agent: {
          ...prev.agent,
          status: agentData.status as any,
          'status-message': agentData['status-message']
        }
      }));
    }
  });

  if (!token) {
    return (
      <main className="flex h-screen w-screen bg-slate-950 text-slate-100 items-center justify-center p-4">
        {authScreen === 'login' ? (
          <LoginForm 
            onSuccess={(accessToken) => setToken(accessToken)} 
            onSwitchToRegister={() => setAuthScreen('register')} 
          />
        ) : (
          <RegisterForm 
            onSuccess={() => setAuthScreen('login')} 
            onSwitchToLogin={() => setAuthScreen('login')} 
          />
        )}
      </main>
    );
  }

  return (
    <main className="flex h-screen w-screen bg-slate-900 text-slate-100 overflow-hidden">
      
      {/* 1. ЛЕВАЯ КОЛОНКА: Чат */}
      <section className="w-[30%] h-full border-r border-slate-800 flex flex-col bg-slate-900/50">
        <div className="p-4 border-b border-slate-800 font-semibold text-sm tracking-wide text-slate-400 flex justify-between items-center">
          <span>ЧАТ: {data.chat.name}</span>
          <button 
            onClick={() => setToken(null)} 
            className="text-xs text-red-400 hover:underline cursor-pointer transition-colors"
          >
            Выйти
          </button>
        </div>
        
        <div className="flex-1 p-4 overflow-y-auto space-y-4">
          {data.chat.history.map((msg) => (
            <div 
              key={msg.id} 
              className={`flex ${msg.from === 'user' ? 'justify-end' : msg.from === 'agent' ? 'justify-start' : 'justify-center'}`}
            >
              {msg.from !== 'user-action' ? (
                <div className={`text-sm p-3 rounded-2xl max-w-[85%] ${
                  msg.from === 'user' 
                    ? 'bg-blue-600 text-white rounded-tr-none' 
                    : 'bg-slate-800 text-slate-200 border border-slate-700 rounded-tl-none'
                }`}>
                  {msg.message}
                </div>
              ) : (
                <span className="text-xs bg-slate-950 px-3 py-1 rounded-full text-slate-500 border border-slate-900">
                  ⚙️ {msg.message}
                </span>
              )}
            </div>
          ))}
        </div>

        <form onSubmit={handleSendMessage} className="p-4 border-t border-slate-800">
          <input 
            type="text" 
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            placeholder="Спросить ИИ-агента..." 
            className="w-full bg-slate-800 text-slate-200 text-sm p-3 rounded-xl outline-none border border-slate-700 focus:border-blue-500 transition-colors"
          />
        </form>
      </section>

            {/* 2. ЦЕНТРАЛЬНАЯ КОЛОНКА: Конструктор параметров (Basic) */}
      <section className="w-[40%] h-full border-r border-slate-800 flex flex-col bg-slate-950">
        <div className="p-4 border-b border-slate-800 font-semibold text-sm tracking-wide text-slate-400 flex justify-between items-center">
          <span>ПАРАМЕТРЫ ПОЕЗДКИ</span>
          <span className="text-xs text-slate-600 bg-slate-900/80 px-2 py-0.5 rounded border border-slate-800">
            Ревизия: {data.revision}
          </span>
        </div>
        
        {/* Контейнер со скроллом, где блоки идут друг за другом */}
        <div className="flex-1 p-6 overflow-y-auto space-y-6">
          
          {/* Блок 1: Отправление */}
          <div className="p-4 bg-slate-900 border border-slate-800 rounded-xl">
            <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-1">Точка отправления</h3>
            <p className="text-sm font-medium text-slate-200">{data.basic.departure}</p>
          </div>

          {/* Блок 2: Состав группы (PeoplesCounter) */}
            <PeoplesCounter 
              peoples={data.basic.peoples}
              onRemoveTag={(tagValue) => {
                setData(prev => ({
                  ...prev,
                  basic: {
                    ...prev.basic,
                    peoples: {
                      ...prev.basic.peoples,
                      'additional-info': prev.basic.peoples['additional-info'].filter(t => t !== tagValue)
                    }
                  }
                }));
              }}
              onUpdateCount={(type, newValue) => {
                setData(prev => ({
                  ...prev,
                  basic: { ...prev.basic, peoples: { ...prev.basic.peoples, [type]: newValue } }
                }));
              }}
            />
          

          {/* Блок 3: Бюджет */}
          <div className="p-4 bg-slate-900 border border-slate-800 rounded-xl">
            <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-1">Оценка бюджета (ИИ)</h3>
            <p className="text-base font-semibold text-emerald-400">{data.basic.cost_range}</p>
          </div>

          {/* ВОТ ЗДЕСЬ (В САМОМ КОНЦЕ) НАХОДИТСЯ БЛОК ДЕРЕВА ЛОКАЦИЙ! */}
          <div className="p-4 bg-slate-900 border border-slate-800 rounded-xl space-y-3">
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
                  setData(prev => ({
                    ...prev,
                    chat: { ...prev.chat, history: [...prev.chat.history, newLogMessage] }
                  }));
                  setTimeout(() => {
                    const agentNotice = {
                      id: Date.now() + 1,
                      from: 'agent' as const,
                      message: `Принято. Я исключил "${fullPath.split('/').pop()}" из планов и перестраиваю варианты отелей.`
                    };
                    setData(prev => ({
                      ...prev,
                      chat: { ...prev.chat, history: [...prev.chat.history, agentNotice] }
                    }));
                  }, 1000);
                }}
              />
            </div>
          </div>

        </div>
      </section>


      {/* 3. ПРАВАЯ КОЛОНКА: Варианты от ИИ */}
      <section className="w-[30%] h-full flex flex-col bg-slate-900/30">
        <div className="p-4 border-b border-slate-800 font-semibold text-sm tracking-wide text-slate-400 flex justify-between items-center">
          <span>ВАРИАНТЫ ОТ ИИ</span>
          <span className="text-xs bg-amber-950/50 border border-amber-800/50 text-amber-400 px-2 py-0.5 rounded animate-pulse">
            {data.agent['status-message']}
          </span>
        </div>
        
        <div className="flex-1 p-4 overflow-y-auto space-y-4">
          {data.variants.map((v) => (
            <div key={v.id} className="p-4 bg-slate-900 border border-slate-800 rounded-xl space-y-3 shadow-md">
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
