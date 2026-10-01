/**
 * Pure logic for the Waifu-Mode dashboard page.
 *
 * Kept free of React (and of the network) so the slider maths and the
 * emoji name→path reducer can be unit-tested directly. The page component
 * only wires these to config GET/save and renders the controls.
 */

/** Slider endpoints (SPEC: 0.5–20 chars/sec). */
export const TYPING_SPEED_MIN = 0.5;
/** Slider MAXIMUM (SPEC: 0.5–20, two decimals). */
export const TYPING_SPEED_MAX = 20;
/**
 * Factory default (chars/sec). 11.12 is the Cyrene anniversary easter egg:
 * the slider opens here and a "default" reset returns to it.
 */
export const TYPING_SPEED_DEFAULT = 11.12;
/** Slider granularity: two decimals, as the SPEC requires. */
export const TYPING_SPEED_STEP = 0.01;

export type EmojiMap = Record<string, string>;

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

/**
 * A non-positive (or malformed) speed means "no delay — send immediately"
 * (`typing_speed = 0`). This is distinct from the 0.5 floor of the slider.
 */
export function isNoDelay(speed: number): boolean {
  return !Number.isFinite(speed) || speed <= 0;
}

/** Snap an arbitrary stored value onto the legal grid, preserving the 0 sentinel. */
export function clampTypingSpeed(speed: number): number {
  if (isNoDelay(speed)) return 0;
  return round2(Math.min(TYPING_SPEED_MAX, Math.max(TYPING_SPEED_MIN, speed)));
}

/**
 * The value the "default" reset button should apply: the backend-served
 * default when it is a usable positive speed, else the built-in 11.12.
 * (Render whatever the config API serves; 11.12 only backstops a missing key.)
 */
export function defaultTypingSpeed(servedDefault: unknown): number {
  return typeof servedDefault === "number" && !isNoDelay(servedDefault)
    ? clampTypingSpeed(servedDefault)
    : TYPING_SPEED_DEFAULT;
}

/**
 * Slider position for a stored speed. The no-delay sentinel (0) parks the
 * handle at the minimum; the separate "no delay" toggle owns that state.
 */
export function speedToSlider(speed: number): number {
  return isNoDelay(speed) ? TYPING_SPEED_MIN : clampTypingSpeed(speed);
}

/** Stored speed for a slider position; clamped into 0.5–20, two decimals. */
export function sliderToSpeed(slider: number): number {
  if (!Number.isFinite(slider)) return TYPING_SPEED_MIN;
  return round2(Math.min(TYPING_SPEED_MAX, Math.max(TYPING_SPEED_MIN, slider)));
}

/** Sorted names→paths view of the emoji map (blank names dropped). */
export function emojiEntries(emojis: EmojiMap): Array<{ name: string; path: string }> {
  return Object.entries(emojis)
    .filter(([name]) => name.trim().length > 0)
    .map(([name, path]) => ({ name, path }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

/**
 * Insert/overwrite one name→path pair. A blank name is rejected and the map is
 * returned unchanged, so an accidental empty row can never enter the config.
 */
export function upsertEmoji(emojis: EmojiMap, name: string, path: string): EmojiMap {
  const key = name.trim();
  if (!key) return emojis;
  return { ...emojis, [key]: path };
}

export function removeEmoji(emojis: EmojiMap, name: string): EmojiMap {
  if (!(name in emojis)) return emojis;
  const next = { ...emojis };
  delete next[name];
  return next;
}

/** Candidate schema keys for an emoji-sending enable switch; first present wins. */
const EMOJI_ENABLE_KEYS = [
  "waifu.emojis_enabled",
  "waifu.emoji_enabled",
  "waifu.stickers_enabled",
] as const;

/**
 * The schema-exposed emoji enable key, or null when the backend doesn't serve
 * one — the page hides the switch rather than inventing a key.
 */
export function resolveEmojiEnableKey(
  schema: Record<string, unknown> | null | undefined,
): string | null {
  if (!schema) return null;
  for (const key of EMOJI_ENABLE_KEYS) {
    if (key in schema) return key;
  }
  return null;
}

/** True when the served config schema knows this key (controls gate on it). */
export function hasSchemaKey(
  schema: Record<string, unknown> | null | undefined,
  key: string,
): boolean {
  return !!schema && key in schema;
}

/**
 * A path usable directly as an <img src>. Anything else is a server-side file
 * path and must be fetched through the managed-files API for preview.
 */
export function isDirectImageSrc(path: string): boolean {
  return /^(https?:|data:image\/|blob:)/i.test(path.trim());
}
