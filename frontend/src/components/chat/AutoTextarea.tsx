'use client';

import { useLayoutEffect, useRef } from 'react';

export default function AutoTextarea({ value, onChange, disabled, placeholder = 'Напишите сообщение…', label = 'Сообщение агенту', id, className = '' }: {
  value: string; onChange: (value: string) => void; disabled?: boolean; placeholder?: string; label?: string; id?: string; className?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    element.style.height = 'auto';
    const limit = Math.max(120, Math.min(360, window.innerHeight * 0.45));
    element.style.height = `${Math.min(element.scrollHeight, limit)}px`;
    element.style.overflowY = element.scrollHeight > limit ? 'auto' : 'hidden';
  }, [value]);
  return <textarea ref={ref} id={id} aria-label={label} value={value} onChange={event => onChange(event.target.value)}
    disabled={disabled} rows={1} maxLength={10000} placeholder={placeholder}
    onKeyDown={event => {
      if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
        event.preventDefault();
        if (value.trim() && !disabled) event.currentTarget.form?.requestSubmit();
      }
    }} className={`app-scrollbar resize-none ${className}`} />;
}
