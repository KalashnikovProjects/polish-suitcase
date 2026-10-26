import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* Твои исходные параметры для сохранения стилей и производительности */
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },

  /* Новые правила: проксирование запросов к бэкенду, чтобы убрать ошибку с JSON */
  async rewrites() {
    const backendUrl = (process.env.BACKEND_INTERNAL_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
    return [
      {
        source: '/api/v1/:path*',
        destination: `${backendUrl}/api/v1/:path*`,
      }
    ];
  },
};

export default nextConfig;
