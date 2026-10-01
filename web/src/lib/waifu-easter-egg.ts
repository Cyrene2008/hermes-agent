/**
 * Reveal/hide rules for the Waifu page's Q版 Cyrene sticker — the hidden
 * 「彩蛋（私货）」 in the page's bottom-right corner.
 *
 * The trigger is a 24px sparkle button and the sticker is a 128px image. The
 * first implementation kept a single `open` boolean flipped by `onMouseEnter` /
 * `onMouseLeave` handlers **on the button itself**, and conditionally mounted
 * the sticker as a sibling inside the same bottom-anchored sticky column. That
 * made the reveal self-defeating: mounting the 128px sticker grew the column
 * upward, the button slid out from under the stationary cursor, `mouseleave`
 * fired, the sticker unmounted, the button fell back under the cursor, and the
 * cycle repeated — a layout-driven flicker loop that happened to end hidden
 * (「鼠标停上去就闪烁了一下，然后不见了」). `onClick` made it worse: it toggled
 * the same boolean, so the click that followed a hover hid what the hover had
 * just revealed, and no further `mouseenter` ever arrived to bring it back.
 *
 * The rules below are a small state machine with two *independent* latches:
 *
 *   - `engaged` — transient: the pointer is over the trigger-or-sticker, or the
 *     trigger holds keyboard focus. Leaving clears it.
 *   - `pinned`  — latched: a tap/press opened the sticker (touch has no hover to
 *     hold it open). Only an explicit dismissal clears it.
 *
 * `visible = engaged || pinned`, so a leave can never cancel a tap and a click
 * can never cancel a hover. Kept React-free so the reveal/hide rules are
 * unit-testable without a DOM.
 */

export interface EasterEggState {
  /** Pointer (mouse) or keyboard focus is on the trigger-or-sticker region. */
  engaged: boolean;
  /** A tap/pen press latched the sticker open until it is dismissed. */
  pinned: boolean;
}

/** Both latches off — the sticker is faded out. */
export const EASTER_EGG_HIDDEN: EasterEggState = {
  engaged: false,
  pinned: false,
};

export type EasterEggAction =
  /** Pointer entered the trigger-or-sticker region (or the trigger got focus). */
  | { type: "engage" }
  /** Pointer left the region (or focus was lost). Never clears a pin. */
  | { type: "disengage" }
  /** Tap/press on the trigger: latch open, or unlatch when already pinned. */
  | { type: "toggle" }
  /** Press outside the region: drop the pin so the sticker fades out. */
  | { type: "dismiss" };

export function easterEggReducer(
  state: EasterEggState,
  action: EasterEggAction,
): EasterEggState {
  switch (action.type) {
    case "engage":
      // Idempotent: a repeated engage returns the same object, so a re-entrant
      // event burst (enter/enter) cannot cause an extra render.
      return state.engaged ? state : { ...state, engaged: true };
    case "disengage":
      // Only clears the transient latch — a tap-pinned sticker stays up until
      // an explicit dismissal, which is what makes "tap to show" usable on
      // touch (the browser fires a synthetic leave after the tap).
      return state.engaged ? { ...state, engaged: false } : state;
    case "toggle":
      return { ...state, pinned: !state.pinned };
    case "dismiss":
      return state.pinned ? { ...state, pinned: false } : state;
  }
}

/** The sticker is shown while either latch is on. */
export function isEasterEggVisible(state: EasterEggState): boolean {
  return state.engaged || state.pinned;
}

/**
 * Whether this pointer type drives the hover latch.
 *
 * Mouse only. A touch tap also emits `pointerenter`/`pointerleave` (plus the
 * compat `mouseover`/`mouseout` pair), and mobile browsers leave a *sticky*
 * `:hover` behind after a tap — if a tap engaged the hover latch, the sticker
 * would stay up after the dismissing tap and "tap outside to hide" could never
 * fire. Touch and pen therefore latch through an explicit press instead.
 */
export function isHoverPointer(pointerType: string): boolean {
  return pointerType === "mouse";
}

/**
 * Whether this pointer type latches the sticker open on press. Touch and pen
 * have no reliable hover, so a press is the only reveal gesture they get; a
 * mouse click is deliberately *not* an activation, so clicking the trigger can
 * never hide the sticker that hovering just revealed.
 */
export function isTapPointer(pointerType: string): boolean {
  return pointerType !== "mouse";
}
