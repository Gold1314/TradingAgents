import { useQuery } from '@tanstack/react-query';
import { apiClient } from '../services/apiClient';
import type { RunConfig } from '../models/run';

/** `GET /api/config` — form defaults. Non-fatal: the form works with built-ins. */
export function useConfig() {
  return useQuery<RunConfig>({
    queryKey: ['config'],
    queryFn: () => apiClient.fetchConfig(),
  });
}
