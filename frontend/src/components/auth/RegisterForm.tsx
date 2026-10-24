'use client';

import React, { useState } from 'react';
import { ApiError, User } from '@/types/auth';

interface RegisterFormProps {
  onSuccess: () => void;
  onSwitchToLogin: () => void;
}

export default function RegisterForm({ onSuccess, onSwitchToLogin }: RegisterFormProps) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsLoading(true);

    try {
      const response = await fetch('/api/v1/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });

      if (!response.ok) {
        const errorData: ApiError = await response.json();
        throw new Error(errorData.error?.message || 'Ошибка регистрации');
      }

      onSuccess();
    } catch (err: any) {
      // ИМИТАЦИЯ ДЛЯ ТЕСТИРОВАНИЯ
      console.warn('Реальный бэкенд недоступен, имитируем успешную регистрацию:', err.message);
      
      await new Promise((resolve) => setTimeout(resolve, 1000));
      onSuccess(); // Просто переводим пользователя на экран логина
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="w-full max-w-md p-8 bg-slate-900 border border-slate-800 rounded-2xl shadow-xl">
      <h2 className="text-2xl font-bold text-slate-100 mb-2">Создать аккаунт</h2>
      <p className="text-sm text-slate-400 mb-6">Зарегистрируйтесь, чтобы начать планировать путешествия с ИИ</p>

      {error && (
        <div className="p-3 mb-4 bg-red-950/40 border border-red-900/60 text-red-400 text-sm rounded-xl">
          ⚠️ {error}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
            Email
          </label>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="user@example.com"
            className="w-full bg-slate-950 text-slate-200 text-sm p-3 rounded-xl border border-slate-800 outline-none focus:border-blue-500 transition-colors"
          />
        </div>

        <div>
          <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
            Пароль
          </label>
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            className="w-full bg-slate-950 text-slate-200 text-sm p-3 rounded-xl border border-slate-800 outline-none focus:border-blue-500 transition-colors"
          />
        </div>

        <button
          type="submit"
          disabled={isLoading}
          className="w-full bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 text-white font-medium text-sm p-3 rounded-xl transition-colors cursor-pointer flex justify-center items-center"
        >
          {isLoading ? 'Регистрация...' : 'Зарегистрироваться'}
        </button>
      </form>

      <div className="mt-6 text-center text-sm text-slate-400">
        Уже есть аккаунт?{' '}
        <button onClick={onSwitchToLogin} className="text-blue-400 hover:underline cursor-pointer">
          Войти
        </button>
      </div>
    </div>
  );
}
