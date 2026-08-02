import { useQuery } from "@tanstack/react-query";
import { getHealthApiHealthGet, type HealthResponse } from "@research-os/api-client";

async function fetchHealth(): Promise<HealthResponse> {
  const { data } = await getHealthApiHealthGet({ throwOnError: true });
  return data;
}

/** Polls `GET /api/health` and returns the per-capability readiness map (TRD §2.2). */
export function useReadiness() {
  return useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 2000,
  });
}
