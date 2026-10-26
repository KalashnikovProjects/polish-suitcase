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
    return [
      {
        source: '/api/v1/:path*',
        destination: 'http://127.0.0*',
      },
      {
        source: '/internal/v1/:path*',
        destination: 'http://127.0.0*',
      }
    ];
  },
};

export default nextConfig;
