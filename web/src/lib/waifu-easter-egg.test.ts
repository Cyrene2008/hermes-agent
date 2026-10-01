/**
 * Reveal/hide rules for the Waifu page's Q版 Cyrene easter-egg sticker.
 *
 * The regression these lock down: the old version kept one `open` boolean on
 * the trigger button and flipped it from `onMouseEnter`/`onMouseLeave`/`onClick`,
 * so (a) hovering then clicking hid what hovering had revealed, and (b) showing
 * the sticker moved the button out from under the cursor, which fired
 * `mouseleave` and hid it again — 「鼠标停上去就闪烁了一下，然后不见了」.
 */
import { describe, expect, it } from "vitest";

import {
  EASTER_EGG_HIDDEN,
  easterEggReducer,
  isEasterEggVisible,
  isHoverPointer,
  isTapPointer,
  type EasterEggAction,
  type EasterEggState,
} from "./waifu-easter-egg";

const collect = (
  actions: EasterEggAction[],
  from: EasterEggState = EASTER_EGG_HIDDEN,
): EasterEggState => actions.reduce(easterEggReducer, from);

const visibleAfter = (actions: EasterEggAction[]): boolean =>
  isEasterEggVisible(collect(actions));

describe("easter egg visibility", () => {
  it("reveals while the pointer is on the trigger and hides when it leaves", () => {
    expect(visibleAfter([])).toBe(false);
    expect(visibleAfter([{ type: "engage" }])).toBe(true);
    expect(visibleAfter([{ type: "engage" }, { type: "disengage" }])).toBe(false);
  });

  it("stays visible when the pointer moves onto the sticker itself", () => {
    // Moving trigger → sticker emits no leave (the sticker is a DOM descendant,
    // so there is one continuous hover region), and hovering the larger sticker
    // may emit extra enters. Neither may hide it.
    expect(
      visibleAfter([
        { type: "engage" },
        { type: "engage" },
        { type: "engage" },
      ]),
    ).toBe(true);
  });

  it("never lets an event flip visibility twice, so hover cannot oscillate", () => {
    const engaged = collect([{ type: "engage" }]);
    const pinned = collect([{ type: "toggle" }]);

    // Idempotence: repeating an event is an identity, not a toggle. The old
    // boolean toggle is exactly what turned one stray event into a flicker.
    expect(easterEggReducer(engaged, { type: "engage" })).toBe(engaged);
    expect(easterEggReducer(EASTER_EGG_HIDDEN, { type: "disengage" })).toBe(
      EASTER_EGG_HIDDEN,
    );
    expect(easterEggReducer(EASTER_EGG_HIDDEN, { type: "dismiss" })).toBe(
      EASTER_EGG_HIDDEN,
    );
    // A pin is orthogonal to hover: engage/disengage only move the hover latch
    // and must never hide the sticker the pin holds up.
    const reengaged = easterEggReducer(pinned, { type: "engage" });
    expect(reengaged).toEqual({ engaged: true, pinned: true });
    expect(visibleAfter([{ type: "toggle" }, { type: "engage" }])).toBe(true);
    expect(isEasterEggVisible(easterEggReducer(reengaged, { type: "disengage" }))).toBe(
      true,
    );

    // A long alternating stream ends exactly where the last event says, with no
    // accumulated flapping in between.
    const stream: EasterEggAction[] = [];
    for (let i = 0; i < 20; i += 1) {
      stream.push({ type: "engage" });
      expect(visibleAfter(stream)).toBe(true);
      stream.push({ type: "disengage" });
      expect(visibleAfter(stream)).toBe(false);
    }
  });

  it("keeps the sticker up after a hover that ends in a click (mouse)", () => {
    // A mouse press is not an activation, so the click that follows a hover
    // cannot hide the sticker; only leaving can.
    expect(isTapPointer("mouse")).toBe(false);
    expect(visibleAfter([{ type: "engage" }, { type: "engage" }])).toBe(true);
    // …and leaving right after the click still hides it — no stuck-open state.
    expect(
      visibleAfter([{ type: "engage" }, { type: "disengage" }]),
    ).toBe(false);
  });
});

describe("easter egg touch path", () => {
  it("latches on tap and survives the synthetic leave browsers send after it", () => {
    expect(
      visibleAfter([{ type: "toggle" }, { type: "disengage" }]),
    ).toBe(true);
  });

  it("hides on tap outside (dismiss drops the pin)", () => {
    expect(
      visibleAfter([
        { type: "toggle" },
        { type: "disengage" },
        { type: "dismiss" },
      ]),
    ).toBe(false);
  });

  it("unlatches when the trigger is tapped again", () => {
    expect(visibleAfter([{ type: "toggle" }, { type: "toggle" }])).toBe(false);
  });
});

describe("pointer-kind gating", () => {
  it("drives hover from a mouse only", () => {
    expect(isHoverPointer("mouse")).toBe(true);
    // Touch taps emit enter/leave too, and mobile browsers leave a sticky
    // :hover behind — a hover latch would survive the tap-outside dismissal.
    expect(isHoverPointer("touch")).toBe(false);
    expect(isHoverPointer("pen")).toBe(false);
    expect(isHoverPointer("")).toBe(false);
  });

  it("latches from a touch/pen press, never from a mouse click", () => {
    expect(isTapPointer("touch")).toBe(true);
    expect(isTapPointer("pen")).toBe(true);
    expect(isTapPointer("mouse")).toBe(false);
  });
});
