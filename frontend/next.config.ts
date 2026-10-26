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
};

export default nextConfig;
