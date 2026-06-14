import { QueryClient } from '@tanstack/react-query';

/** Shared TanStack Query client for REST reads (`/api/config`, `/api/chart`). */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 60_000,
      refetchOnWindowFocus: false,
    },
  },
});
