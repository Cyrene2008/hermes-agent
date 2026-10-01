// @vitest-environment jsdom
/**
 * Wiring tests for the Waifu page's Q版 Cyrene easter egg (the 「彩蛋（私货）」
 * corner sticker). The rules themselves are unit-tested in
 * `lib/waifu-easter-egg.test.ts`; this file pins the DOM contract that the old
 * implementation broke:
 *
 *  - the sticker stays mounted and only fades, so revealing it can never move
 *    the trigger (the layout-shift flicker loop),
 *  - hover is tracked on the whole region, so moving onto the sticker keeps it,
 *  - a mouse click cannot hide what hover just revealed,
 *  - a touch tap latches it open and a press outside dismisses it,
 *  - the asset URL honours a non-root HERMES_BASE_PATH.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({ HERMES_BASE_PATH: "/hermes" }));

import { WaifuEasterEgg } from "./WaifuEasterEgg";

let container: HTMLDivElement;
let root: Root;

function pointerEvent(
  type: string,
  pointerType: string,
  init: MouseEventInit = {},
): Event {
  // jsdom has no PointerEvent; React only reads `type` and `pointerType`, so a
  // MouseEvent carrying the extra field is enough to exercise the handlers.
  const event = new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    ...init,
  });
  Object.defineProperty(event, "pointerType", { value: pointerType });
  return event;
}

async function dispatch(target: EventTarget, event: Event): Promise<void> {
  await act(async () => {
    target.dispatchEvent(event);
  });
}

const region = () =>
  container.querySelector<HTMLElement>('[data-testid="waifu-easter-egg"]')!;
const trigger = () => region().querySelector("button")!;
const sticker = () =>
  container.querySelector<HTMLImageElement>(
    '[data-testid="waifu-easter-egg-sticker"]',
  )!;
const isVisible = () => sticker().dataset.visible === "true";

async function mount(ui: ReactNode = <WaifuEasterEgg label="Cyrene" />) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

beforeEach(() => {
  document.body.innerHTML = "";
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
});

describe("WaifuEasterEgg", () => {
  it("ships the sticker behind the HERMES_BASE_PATH prefix, faded out at rest", async () => {
    await mount();
    expect(sticker()).toBeTruthy();
    expect(sticker().getAttribute("src")).toBe("/hermes/cyrene_easter_egg.png");
    expect(isVisible()).toBe(false);
    expect(trigger().getAttribute("aria-expanded")).toBe("false");
  });

  it("reveals on mouse hover and hides only once the pointer is off the region", async () => {
    await mount();
    await dispatch(
      region(),
      pointerEvent("pointerover", "mouse", { relatedTarget: document.body }),
    );
    expect(isVisible()).toBe(true);
    expect(trigger().getAttribute("aria-expanded")).toBe("true");

    // Moving onto the sticker is still inside the hover region.
    await dispatch(
      region(),
      pointerEvent("pointerout", "mouse", { relatedTarget: sticker() }),
    );
    expect(isVisible()).toBe(true);

    await dispatch(
      region(),
      pointerEvent("pointerout", "mouse", { relatedTarget: document.body }),
    );
    expect(isVisible()).toBe(false);
  });

  it("keeps the same sticker node across the reveal/hide cycle", async () => {
    await mount();
    const before = sticker();
    await dispatch(
      region(),
      pointerEvent("pointerover", "mouse", { relatedTarget: document.body }),
    );
    expect(sticker()).toBe(before);
    await dispatch(
      region(),
      pointerEvent("pointerout", "mouse", { relatedTarget: document.body }),
    );
    // Never unmounted: the image cannot pop in and out of the layout.
    expect(sticker()).toBe(before);
    expect(sticker().className).toContain("absolute");
    expect(sticker().className).toContain("opacity-0");
  });

  it("does not hide the sticker when the hovered trigger is clicked", async () => {
    await mount();
    await dispatch(
      region(),
      pointerEvent("pointerover", "mouse", { relatedTarget: document.body }),
    );
    await dispatch(trigger(), new MouseEvent("click", { bubbles: true }));
    expect(isVisible()).toBe(true);
  });

  it("reveals on keyboard focus and hides when focus leaves", async () => {
    await mount();
    await dispatch(
      trigger(),
      new FocusEvent("focusin", { bubbles: true }),
    );
    expect(isVisible()).toBe(true);
    await dispatch(
      trigger(),
      new FocusEvent("focusout", { bubbles: true }),
    );
    expect(isVisible()).toBe(false);
  });

  it("latches on a touch tap and dismisses on a press outside", async () => {
    await mount();
    await dispatch(trigger(), pointerEvent("pointerup", "touch"));
    expect(isVisible()).toBe(true);

    // The leave a phone sends right after the tap must not cancel the latch.
    await dispatch(
      region(),
      pointerEvent("pointerout", "touch", { relatedTarget: document.body }),
    );
    expect(isVisible()).toBe(true);

    // A press outside the region (the listener is a document-level one).
    await dispatch(
      document.body,
      pointerEvent("pointerdown", "touch", { relatedTarget: document.body }),
    );
    expect(isVisible()).toBe(false);
  });

  it("keeps a latched sticker when the outside press starts on the sticker", async () => {
    await mount();
    await dispatch(trigger(), pointerEvent("pointerup", "touch"));
    await dispatch(sticker(), pointerEvent("pointerdown", "touch"));
    expect(isVisible()).toBe(true);
  });

  it("ignores touch enter/leave so a sticky mobile hover cannot pin it", async () => {
    await mount();
    await dispatch(
      region(),
      pointerEvent("pointerover", "touch", { relatedTarget: document.body }),
    );
    expect(isVisible()).toBe(false);
  });
});
