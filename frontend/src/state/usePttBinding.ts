import { useQuery } from "@tanstack/react-query";
import { getVoiceSettingsApiSettingsVoiceGet } from "@research-os/api-client";

/** Reads the push-to-talk binding from Settings Store (Voice Layer Plan
 * V12) — `useVoice`'s talk-key listener reads this instead of a hardcoded
 * chord, so a rebind in Settings takes effect without a restart. Polled at
 * the same cadence `useReadiness` already uses for a similarly
 * low-frequency setting, rather than wiring a bespoke invalidation path
 * from `VoiceSettingsForm`'s save into every mounted `useVoice` instance. */
export function usePttBinding() {
  return useQuery({
    queryKey: ["voice-settings", "ptt-binding"],
    queryFn: async () => {
      const { data } = await getVoiceSettingsApiSettingsVoiceGet({ throwOnError: true });
      return data.voice_ptt_binding;
    },
    refetchInterval: 5000,
  });
}
