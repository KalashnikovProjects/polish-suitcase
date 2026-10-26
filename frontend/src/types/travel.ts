// Операции формата JSON Patch (RFC 6902)
export interface PatchOperation {
  op: 'add' | 'remove' | 'replace' | 'move' | 'copy' | 'test';
  path: string;
  value?: any;
}

// Одно сообщение в чате
export interface ChatMessage {
  id: number;
  from: 'user' | 'agent' | 'user-action';
  message?: string;
  operations?: PatchOperation[];
}

export interface ChatSummary {
  id: number;
  name: string;
  updated_at: string;
}

// Динамические характеристики (ключ-значение)
export interface DynamicAttribute {
  label: string;
  value: string;
}

// Проживание / Отели
export interface Habitation {
  id: number;
  name: string;
  description: string;
  dates: {
    from: string;
    to: string;
  };
  url: string;
  price: string;
  additional?: DynamicAttribute[];
}

// Транспорт
export interface Transport {
  id: number;
  name: string;
  type: string; // например, 'flight'
  price: string;
  times?: {
    from?: string;
    to?: string;
    duration?: string;
  };
  additional?: DynamicAttribute[];
}

// Карточка варианта путешествия
export interface TravelVariant {
  id: number;
  name: string;
  location: string;
  price: string;
  dates: {
    from: string;
    to: string;
  };
  completed: boolean;
  description: string;
  urls: string[];
  habitations: Habitation[];
  transports: Transport[];
  additional?: DynamicAttribute[];
}

// Кастомные поля в секции дополнительных параметров
export interface DynamicField {
  id: string;
  type: 'number' | 'tags' | 'string' | string;
  label: string;
  value: any;
}

// Группа дополнительных параметров (например, ограничения по городам)
export interface AdditionalGroup {
  id: string;
  title: string;
  fields: DynamicField[];
}

// ГЛАВНЫЙ ИНТЕРФЕЙС ВСЕЙ СТРУКТУРЫ
export interface TravelResponse {
  id: number;
  revision: number; // Номер ревизии для синхронизации версий
  chat: {
    name: string;
  };
  history: ChatMessage[];
  basic: {
    departure: string;
    dates: {
      from: string;
      to: string;
    };
    peoples: {
      adults: number;
      children: number;
      unknown: number;
      'additional-info': string[];
    };
    price_restrictions: {
      min: number;
      max: number;
    };
    cost_range: string;
    locations_tree: Record<string, any>; // Вложенный объект дерева локаций
  };
  variants: TravelVariant[];
  additional: AdditionalGroup[];
  additional_hidden?: Record<string, any>; // Скрытые данные агента (игнорируем в UI)
  agent: {
    status: 'idle' | 'working' | 'waiting_user' | 'completed' | 'error';
    'status-message': string;
  };
}
