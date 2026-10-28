export interface CartLine {
  id: string;
  kind: 'hotel' | 'transport';
  name: string;
  price: string;
  selected: boolean;
  type?: string;
  dates?: { from: string; to: string };
  times?: { from?: string; to?: string; duration?: string };
}

export interface CartItem {
  id: string;
  chat_id: number;
  variant_id: number;
  name: string;
  location: string;
  dates: { from: string; to: string };
  lines: CartLine[];
  added_at: string;
  stale: boolean;
}

export interface Cart {
  items: CartItem[];
  selected_count: number;
  demo_only: true;
}

export interface DemoOrder {
  id: string;
  status: 'paid_demo';
  paid_at: string;
  message: string;
  items: Array<{ chat_id: number; variant_id: number; name: string; lines: CartLine[] }>;
}
