const months = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

function dateParts(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return null;
  const [, y, m, d] = match.map(Number);
  const check = new Date(Date.UTC(y, m - 1, d));
  return check.getUTCFullYear() === y && check.getUTCMonth() === m - 1 && check.getUTCDate() === d ? { y, m, d } : null;
}
export function formatDate(value: string) {
  const parts = dateParts(value);
  return parts ? `${parts.d} ${months[parts.m - 1]} ${parts.y}` : value === '-' || !value ? 'Не выбрано' : value;
}
export function formatDateTime(value = '') {
  const time = /[T ](\d{2}:\d{2})/.exec(value);
  return `${formatDate(value)}${time ? `, ${time[1]}` : ''}`;
}
export function dateInput(value: string) {
  const p = dateParts(value);
  return p ? `${String(p.d).padStart(2, '0')}.${String(p.m).padStart(2, '0')}.${p.y}` : '';
}
export function parseDateInput(value: string): string | null {
  if (!value.trim()) return '-';
  const match = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(value.trim());
  const iso = match ? `${match[3]}-${match[2]}-${match[1]}` : value;
  return /^\d{4}-\d{2}-\d{2}$/.test(iso) && dateParts(iso) ? iso : null;
}
export function userText(value: string) {
  return value
    .replace(/checked_baggage requirement is not fully confirmed by synthetic transport source/gi, 'Условия провоза багажа нужно уточнить.')
    .replace(/(?:Покрытие источника|покрытие поиска|покрытие)\s*[:—-]?\s*(?:unsupported|partial)\.?/gi, '')
    .replace(/Недостаточно покрытия:[^\n.?!]*/g, 'По этому направлению доступны не все варианты дороги.')
    .replace(/(?:Данные\s+)?(?:synthetic|sandbox)(?:; цены и наличие тестовые)?/gi, 'демонстрационные цены')
    .replace(/\b\d{4}-\d{2}-\d{2}\b/g, formatDate).trim();
}
export function providerName(value: string) {
  return ({ liteapi: 'LiteAPI', yandex_rasp: 'Яндекс Расписания', synthetic_v1: 'Демонстрация' } as Record<string, string>)[value] || value;
}
