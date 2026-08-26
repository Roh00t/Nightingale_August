import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  experimental: {
    serverActions: {
      bodySizeLimit: "2mb",
    },
    optimizePackageImports: ['lucide-react', 'date-fns'],
  },
};

export default nextConfig;
