import { describe, expect, it } from "vitest";

import {
  TYPING_SPEED_DEFAULT,
  TYPING_SPEED_MAX,
  TYPING_SPEED_MIN,
  clampTypingSpeed,
  defaultTypingSpeed,
  emojiEntries,
  hasSchemaKey,
  isDirectImageSrc,
  isNoDelay,
  removeEmoji,
  resolveEmojiEnableKey,
  sliderToSpeed,
  speedToSlider,
  upsertEmoji,
} from "./waifu-page";

describe("typing-speed slider maths", () => {
  it("runs 0.5–20 with the 11.12 anniversary default", () => {
    expect(TYPING_SPEED_MAX).toBe(20);
    expect(TYPING_SPEED_MIN).toBe(0.5);
    expect(TYPING_SPEED_DEFAULT).toBe(11.12);
    // The slider maximum is 20, payable in hundredths.
    expect(sliderToSpeed(20)).toBe(20);
    expect(sliderToSpeed(100)).toBe(20);
    // The default sits inside the range at the anniversary value.
    expect(sliderToSpeed(TYPING_SPEED_DEFAULT)).toBe(11.12);
    expect(sliderToSpeed(11.125)).toBe(11.13);
  });

  it("slider round-trips a stored speed inside the range", () => {
    expect(speedToSlider(11.12)).toBe(11.12);
    expect(sliderToSpeed(speedToSlider(11.12))).toBe(11.12);
    expect(sliderToSpeed(speedToSlider(20))).toBe(20);
    expect(sliderToSpeed(speedToSlider(0.5))).toBe(0.5);
  });

  it("resolves the reset/default button value from the served default", () => {
    expect(defaultTypingSpeed(11.12)).toBe(11.12);
    expect(defaultTypingSpeed(4)).toBe(4);
    // Missing/zero/malformed served default falls back to the 11.12 easter egg.
    expect(defaultTypingSpeed(0)).toBe(11.12);
    expect(defaultTypingSpeed(undefined)).toBe(11.12);
    expect(defaultTypingSpeed("nope")).toBe(11.12);
  });

  it("treats 0 (and malformed values) as no-delay, not as the 0.5 floor", () => {
    expect(isNoDelay(0)).toBe(true);
    expect(isNoDelay(-4)).toBe(true);
    expect(isNoDelay(Number.NaN)).toBe(true);
    expect(isNoDelay(0.5)).toBe(false);
    // The no-delay sentinel must survive clamping.
    expect(clampTypingSpeed(0)).toBe(0);
    // Slider parks at the minimum for a stored 0; the separate toggle owns no-delay.
    expect(speedToSlider(0)).toBe(TYPING_SPEED_MIN);
    // Using the slider clears no-delay: even the leftmost position is a real speed.
    expect(sliderToSpeed(TYPING_SPEED_MIN)).toBe(0.5);
  });

  it("clamps out-of-range speeds into the legal grid", () => {
    expect(clampTypingSpeed(99)).toBe(20);
    expect(clampTypingSpeed(0.2)).toBe(0.5);
    expect(clampTypingSpeed(5.678)).toBe(5.68);
    // +∞ is "infinitely fast" — it collapses to the no-delay sentinel.
    expect(clampTypingSpeed(Number.POSITIVE_INFINITY)).toBe(0);
  });
});

describe("emoji map reducer", () => {
  it("upserts a trimmed name and overwrites an existing mapping", () => {
    const base = { happy: "/a.png" };
    expect(upsertEmoji(base, " sad ", "/b.png")).toEqual({
      happy: "/a.png",
      sad: "/b.png",
    });
    expect(upsertEmoji(base, "happy", "/new.png")).toEqual({ happy: "/new.png" });
  });

  it("rejects a blank name without mutating the map", () => {
    const base = { happy: "/a.png" };
    expect(upsertEmoji(base, "   ", "/x.png")).toBe(base);
    expect(upsertEmoji(base, "", "/x.png")).toBe(base);
  });

  it("removes a mapping and is a no-op for a missing name", () => {
    const base = { happy: "/a.png", sad: "/b.png" };
    expect(removeEmoji(base, "happy")).toEqual({ sad: "/b.png" });
    expect(removeEmoji(base, "nope")).toBe(base);
  });

  it("lists entries sorted by name, dropping blank keys", () => {
    expect(
      emojiEntries({ zoo: "/z.png", abc: "/a.png", "  ": "/drop.png" }),
    ).toEqual([
      { name: "abc", path: "/a.png" },
      { name: "zoo", path: "/z.png" },
    ]);
  });

  it("does not mutate the input map", () => {
    const base = { happy: "/a.png" };
    upsertEmoji(base, "sad", "/b.png");
    removeEmoji(base, "happy");
    expect(base).toEqual({ happy: "/a.png" });
  });
});

describe("schema gating", () => {
  it("finds no emoji enable key when the schema omits one", () => {
    expect(resolveEmojiEnableKey({ "waifu.enabled": {} })).toBeNull();
    expect(resolveEmojiEnableKey(null)).toBeNull();
  });

  it("returns the first present emoji enable key", () => {
    expect(
      resolveEmojiEnableKey({ "waifu.emoji_enabled": {}, "waifu.emojis_enabled": {} }),
    ).toBe("waifu.emojis_enabled");
    expect(resolveEmojiEnableKey({ "waifu.stickers_enabled": {} })).toBe(
      "waifu.stickers_enabled",
    );
  });

  it("hasSchemaKey answers presence, not truthiness", () => {
    expect(hasSchemaKey({ "waifu.immersive": {} }, "waifu.immersive")).toBe(true);
    expect(hasSchemaKey({}, "waifu.immersive")).toBe(false);
    expect(hasSchemaKey(null, "waifu.immersive")).toBe(false);
  });
});

describe("isDirectImageSrc", () => {
  it("accepts browser-loadable sources and rejects filesystem paths", () => {
    expect(isDirectImageSrc("https://cdn.example/x.png")).toBe(true);
    expect(isDirectImageSrc("data:image/png;base64,AAAA")).toBe(true);
    expect(isDirectImageSrc("/home/me/stickers/x.png")).toBe(false);
    expect(isDirectImageSrc("~/pics/x.png")).toBe(false);
  });
});
