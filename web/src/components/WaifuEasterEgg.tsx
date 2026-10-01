import { useEffect, useReducer, useRef } from "react";
import { Sparkles } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { HERMES_BASE_PATH } from "@/lib/api";
import {
  EASTER_EGG_HIDDEN,
  easterEggReducer,
  isEasterEggVisible,
  isHoverPointer,
  isTapPointer,
} from "@/lib/waifu-easter-egg";

/**
 * The Q版 Cyrene sticker hidden in the Waifu page's bottom-right corner
 * (SPEC 「彩蛋（私货）清单」). Faint sparkle trigger; the sticker fades in while
 * the pointer (or keyboard focus) is on the trigger **or** the sticker, and
 * fades out again when it leaves. Tapping latches it on touch.
 *
 * Two structural rules keep the reveal from flickering — see
 * `lib/waifu-easter-egg.ts` for the full post-mortem of the old
 * hover-boolean-inside-the-button version:
 *
 * 1. Hover is tracked on the whole region, not the 24px button, and the sticker
 *    is a DOM descendant of that region — so "on the trigger or the sticker"
 *    is one continuous hover target.
 * 2. The sticker is absolutely positioned *above* the trigger (and stays
 *    mounted, only fading), so revealing it cannot move the trigger out from
 *    under the cursor. That layout shift was the flicker engine: the button
 *    jumped, `mouseleave` fired, the image unmounted, the button dropped back
 *    under the cursor, and the cycle repeated until it happened to end hidden.
 */
export function WaifuEasterEgg({ label }: { label: string }) {
  const [state, dispatch] = useReducer(easterEggReducer, EASTER_EGG_HIDDEN);
  const regionRef = useRef<HTMLDivElement>(null);
  const visible = isEasterEggVisible(state);

  // Touch/pen dismissal: while latched open, a press anywhere outside the
  // region drops the pin. Registered only while pinned, so the page pays for
  // the document listener just for the lifetime it can actually use it.
  useEffect(() => {
    if (!state.pinned) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!regionRef.current?.contains(event.target as Node)) {
        dispatch({ type: "dismiss" });
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [state.pinned]);

  return (
    <div className="pointer-events-none sticky bottom-3 z-20 flex justify-end">
      <div
        ref={regionRef}
        data-testid="waifu-easter-egg"
        className="pointer-events-auto relative flex items-end"
        onPointerEnter={(event) => {
          if (isHoverPointer(event.pointerType)) dispatch({ type: "engage" });
        }}
        onPointerLeave={(event) => {
          if (isHoverPointer(event.pointerType)) dispatch({ type: "disengage" });
        }}
        onFocus={(event) => {
          // focusin bubbles, so this covers the trigger and anything else in
          // the region; only focus arriving from outside the region engages.
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
            dispatch({ type: "engage" });
          }
        }}
        onBlur={(event) => {
          // Tabbing away hides the sticker; focus moving inside the region
          // (trigger → sticker) must not.
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
            dispatch({ type: "disengage" });
          }
        }}
      >
        <Button
          ghost
          size="icon"
          aria-label={label}
          title={label}
          aria-expanded={visible}
          onPointerUp={(event) => {
            // Mouse clicks are deliberately inert: hover already revealed the
            // sticker, and toggling on click is what used to hide it again the
            // instant the user pressed what they were hovering. Touch and pen
            // have no reliable hover, so a press is their reveal gesture.
            if (isTapPointer(event.pointerType)) dispatch({ type: "toggle" });
          }}
          className="h-6 w-6 text-text-tertiary opacity-40 transition-opacity hover:opacity-100 focus-visible:opacity-100"
        >
          <Sparkles className="h-3.5 w-3.5" />
        </Button>
        <img
          src={`${HERMES_BASE_PATH}/cyrene_easter_egg.png`}
          alt=""
          aria-hidden
          data-testid="waifu-easter-egg-sticker"
          data-visible={visible}
          // Hidden it must not swallow clicks on the page behind it; visible it
          // must be hoverable, or moving onto the sticker would count as
          // leaving the hover region and immediately hide it.
          className={`absolute right-0 bottom-full mb-2 h-32 w-32 drop-shadow-lg transition-opacity duration-200 ease-out motion-reduce:transition-none ${
            visible ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0"
          }`}
          onPointerDown={(event) => event.preventDefault()}
        />
      </div>
    </div>
  );
}
