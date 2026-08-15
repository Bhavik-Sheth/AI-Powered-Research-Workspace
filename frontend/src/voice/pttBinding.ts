/** The push-to-talk binding's string <-> held-modifiers shapes (Voice Layer
 * Plan V12) — shared by `VoiceSettingsForm` (which captures a new binding)
 * and `useVoice` (which matches the live keydown/keyup stream against the
 * stored one). One definition, so a rebind captured here can never diverge
 * from what the talk-key listener recognises.
 */

export const MODIFIER_KEYS = ["Control", "Shift", "Alt", "Meta"] as const;
export type ModifierKey = (typeof MODIFIER_KEYS)[number];

export const MODIFIER_LABEL: Record<ModifierKey, string> = { Control: "Ctrl", Shift: "Shift", Alt: "Alt", Meta: "Meta" };

const LABEL_TO_KEY: Record<string, ModifierKey> = Object.fromEntries(
  MODIFIER_KEYS.map((key) => [MODIFIER_LABEL[key], key]),
) as Record<string, ModifierKey>;

export function isModifierKey(key: string): key is ModifierKey {
  return (MODIFIER_KEYS as readonly string[]).includes(key);
}

/** Renders a held modifier set in the fixed Ctrl/Shift/Alt/Meta order, so
 * the same physical chord always produces the same binding string
 * regardless of press order. */
export function bindingFromHeld(held: ReadonlySet<ModifierKey>): string {
  return MODIFIER_KEYS.filter((key) => held.has(key))
    .map((key) => MODIFIER_LABEL[key])
    .join("+");
}

/** The inverse of `bindingFromHeld` — turns a stored binding string like
 * `"Ctrl+Shift"` back into the `KeyboardEvent.key` values that make it up,
 * so a live keydown/keyup stream can be matched against it. An unparseable
 * token is dropped rather than thrown on, so a corrupt or future-format
 * stored value degrades to "no keys match" instead of crashing the
 * listener. */
export function keysForBinding(binding: string): ReadonlySet<ModifierKey> {
  return new Set(
    binding
      .split("+")
      .map((token) => LABEL_TO_KEY[token.trim()])
      .filter((key): key is ModifierKey => key !== undefined),
  );
}
