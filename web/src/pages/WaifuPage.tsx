import { useCallback, useEffect, useMemo, useState } from "react";
import { Heart, ImageOff, Minus, Plus, Sparkles, Trash2 } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@nous-research/ui/ui/components/card";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Switch } from "@nous-research/ui/ui/components/switch";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { api, HERMES_BASE_PATH } from "@/lib/api";
import type { Translations } from "@/i18n/types";
import { useI18n } from "@/i18n";
import { en } from "@/i18n/en";
import { usePageHeader } from "@/contexts/usePageHeader";
import { getNestedValue, setNestedValue } from "@/lib/nested";
import { errorMessage } from "@/lib/api-error";
import {
  TYPING_SPEED_MAX,
  TYPING_SPEED_MIN,
  TYPING_SPEED_STEP,
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
  type EmojiMap,
} from "@/lib/waifu-page";

type WaifuStrings = NonNullable<Translations["waifu"]>;

/** The interrupt stepper's soft cap; the SPEC only names 0/1/2/3. */
const INTERRUPT_AFTER_MAX = 9;

function toInt(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return 0;
  return Math.max(0, Math.trunc(value));
}

function toEmojiMap(value: unknown): EmojiMap {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return {};
  }
  const out: EmojiMap = {};
  for (const [k, v] of Object.entries(value)) {
    if (typeof v === "string") out[k] = v;
  }
  return out;
}

function formatSpeed(speed: number): string {
  return String(speed);
}

/* ------------------------------------------------------------------ */
/*  Small presentational helpers                                       */
/* ------------------------------------------------------------------ */

function Hint({ children }: { children: string }) {
  if (!children) return null;
  return <span className="text-xs text-text-secondary">{children}</span>;
}

function ToggleRow({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="flex flex-col gap-0.5">
        <Label className="text-sm">{label}</Label>
        {hint ? <Hint>{hint}</Hint> : null}
      </div>
      <Switch checked={checked} onCheckedChange={onChange} />
    </div>
  );
}

/** Lazy thumbnail: URL/data sources render directly, filesystem paths go
 *  through the managed-files API and come back as a data URL. */
function EmojiThumb({ path, fallback }: { path: string; fallback: string }) {
  const [resolved, setResolved] = useState<{
    path: string;
    src: string | null;
    failed: boolean;
  }>({ path: "", src: null, failed: false });

  useEffect(() => {
    if (!path.trim() || isDirectImageSrc(path)) return;
    let cancelled = false;
    api
      .readFile(path)
      .then((resp) => {
        if (!cancelled) setResolved({ path, src: resp.data_url, failed: false });
      })
      .catch(() => {
        if (!cancelled) setResolved({ path, src: null, failed: true });
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  // Derive the render state during render so a path change never needs a
  // synchronous setState inside the effect body.
  const active =
    resolved.path === path
      ? resolved
      : { path, src: isDirectImageSrc(path) ? path : null, failed: false };

  if (active.failed) {
    return (
      <span
        title={fallback}
        className="flex h-10 w-10 shrink-0 items-center justify-center border border-border text-text-tertiary"
      >
        <ImageOff className="h-4 w-4" />
      </span>
    );
  }
  if (!active.src) {
    return (
      <span className="flex h-10 w-10 shrink-0 items-center justify-center border border-border">
        <Spinner className="text-sm text-text-tertiary" />
      </span>
    );
  }
  return (
    <img
      src={active.src}
      alt=""
      onError={() => setResolved({ path, src: null, failed: true })}
      className="h-10 w-10 shrink-0 border border-border object-cover"
    />
  );
}

/** The Q版 Cyrene sticker in a page corner — revealed only on hover/click. */
function EasterEgg({ label }: { label: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="pointer-events-none sticky bottom-3 z-20 flex flex-col items-end gap-2">
      <Button
        ghost
        size="icon"
        aria-label={label}
        title={label}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen((value) => !value)}
        className="pointer-events-auto h-6 w-6 text-text-tertiary opacity-40 transition-opacity hover:opacity-100 focus-visible:opacity-100"
      >
        <Sparkles className="h-3.5 w-3.5" />
      </Button>
      {open && (
        <img
          src={`${HERMES_BASE_PATH}/cyrene_easter_egg.png`}
          alt=""
          aria-hidden
          className="pointer-events-auto h-32 w-32 drop-shadow-lg"
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Page                                                               */
/* ------------------------------------------------------------------ */

export default function WaifuPage() {
  const { t } = useI18n();
  const w: WaifuStrings = t.waifu ?? (en.waifu as WaifuStrings);
  const { toast, showToast } = useToast();
  const { setTitle } = usePageHeader();

  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [schema, setSchema] = useState<Record<string, unknown> | null>(null);
  const [defaults, setDefaults] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [newName, setNewName] = useState("");
  const [newPath, setNewPath] = useState("");

  useEffect(() => {
    setTitle(w.title);
    return () => setTitle(null);
  }, [setTitle, w.title]);

  const loadFailed = w.loadFailed;
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getConfig(), api.getSchema(), api.getDefaults()])
      .then(([cfg, sch, defs]) => {
        if (cancelled) return;
        setConfig(cfg);
        setSchema(sch.fields as Record<string, unknown>);
        setDefaults(defs);
      })
      .catch(() => {
        if (!cancelled) showToast(loadFailed, "error");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [showToast, loadFailed]);

  const updateWaifu = useCallback((key: string, value: unknown) => {
    setConfig((prev) => (prev ? setNestedValue(prev, `waifu.${key}`, value) : prev));
    setDirty(true);
  }, []);

  const handleSave = async () => {
    if (!config) return;
    setSaving(true);
    try {
      await api.saveConfig(config);
      setDirty(false);
      showToast(w.saved, "success");
    } catch (e) {
      showToast(`${w.failedToSave}: ${errorMessage(e)}`, "error");
    } finally {
      setSaving(false);
    }
  };

  const emojis = toEmojiMap(getNestedValue(config ?? {}, "waifu.emojis"));
  const emojiEntriesList = useMemo(() => emojiEntries(emojis), [emojis]);

  const showJitter = hasSchemaKey(schema, "waifu.typing_jitter_ms");
  const showImmersive = hasSchemaKey(schema, "waifu.immersive");
  const emojiEnableKey = resolveEmojiEnableKey(schema);

  if (loading || !config || !schema) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }

  const enabled = getNestedValue(config, "waifu.enabled") === true;
  const rawSpeed = getNestedValue(config, "waifu.typing_speed");
  const speed = typeof rawSpeed === "number" ? rawSpeed : 0;
  const interruptAfter = toInt(getNestedValue(config, "waifu.interrupt_after"));
  const stripPunctuation =
    getNestedValue(config, "waifu.strip_trailing_punctuation") === true;
  const immersive = getNestedValue(config, "waifu.immersive") === true;
  const jitter = getNestedValue(config, "waifu.typing_jitter_ms");
  const emojisEnabled = emojiEnableKey
    ? getNestedValue(config, emojiEnableKey) === true
    : true;

  const noDelay = isNoDelay(speed);
  const servedDefault = getNestedValue(defaults ?? {}, "waifu.typing_speed");
  const resetSpeed = defaultTypingSpeed(servedDefault);

  const addEmoji = () => {
    if (!newName.trim()) return;
    updateWaifu("emojis", upsertEmoji(emojis, newName, newPath));
    setNewName("");
    setNewPath("");
  };

  return (
    <div className="flex flex-col gap-4">
      <Toast toast={toast} />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <Heart className="h-4 w-4 shrink-0 text-muted-foreground" />
          <div className="flex min-w-0 flex-col">
            <span className="text-sm font-medium">{w.title}</span>
            <span className="text-xs text-text-secondary">{w.subtitle}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {dirty && (
            <Badge tone="warning" className="text-xs">
              ●
            </Badge>
          )}
          <Button
            size="sm"
            className="uppercase"
            onClick={() => void handleSave()}
            disabled={saving}
          >
            {saving ? t.common.saving : w.save}
          </Button>
        </div>
      </div>

      <Card>
        <CardContent className="px-4 py-3">
          <ToggleRow
            label={w.masterToggle}
            hint={w.masterToggleHint}
            checked={enabled}
            onChange={(value) => updateWaifu("enabled", value)}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="py-3 px-4">
          <CardTitle className="flex items-center gap-2 text-sm">
            {w.typingSpeed}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 px-4 pb-4">
          <div className="flex flex-wrap items-center gap-3">
            <input
              type="range"
              aria-label={w.typingSpeed}
              min={TYPING_SPEED_MIN}
              max={TYPING_SPEED_MAX}
              step={TYPING_SPEED_STEP}
              value={speedToSlider(speed)}
              disabled={noDelay}
              onChange={(e) =>
                updateWaifu("typing_speed", sliderToSpeed(e.target.valueAsNumber))
              }
              className="h-1.5 min-w-[12rem] flex-1 cursor-pointer accent-midground disabled:cursor-not-allowed disabled:opacity-40"
            />
            <Input
              type="number"
              aria-label={w.typingSpeed}
              min={TYPING_SPEED_MIN}
              max={TYPING_SPEED_MAX}
              step={TYPING_SPEED_STEP}
              value={noDelay ? "" : String(speed)}
              disabled={noDelay}
              placeholder={w.noDelay}
              onChange={(e) => {
                const raw = e.target.value;
                if (raw === "") {
                  updateWaifu("typing_speed", 0);
                  return;
                }
                const next = e.target.valueAsNumber;
                if (!Number.isNaN(next)) {
                  updateWaifu("typing_speed", clampTypingSpeed(next));
                }
              }}
              className="w-24"
            />
            <Badge tone="secondary" className="tabular-nums text-xs">
              {noDelay
                ? w.noDelay
                : w.typingSpeedValue.replace("{value}", formatSpeed(speed))}
            </Badge>
            {showJitter && typeof jitter === "number" && (
              <Badge
                tone="outline"
                className="tabular-nums text-xs"
                title={w.jitterHint}
              >
                {w.jitterBadge.replace("{ms}", String(jitter))}
              </Badge>
            )}
            <Button
              ghost
              size="xs"
              onClick={() => updateWaifu("typing_speed", resetSpeed)}
              title={w.defaultSpeedHint}
            >
              {w.defaultSpeedBadge}
            </Button>
          </div>
          <ToggleRow
            label={w.noDelay}
            hint={w.noDelayHint}
            checked={noDelay}
            onChange={(value) =>
              updateWaifu("typing_speed", value ? 0 : resetSpeed)
            }
          />
          <Hint>{w.typingSpeedHint}</Hint>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="py-3 px-4">
          <CardTitle className="text-sm">{w.interruptAfter}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 px-4 pb-4">
          <div className="flex items-center gap-2">
            <Button
              ghost
              size="icon"
              aria-label={w.interruptImmediate}
              disabled={interruptAfter <= 0}
              onClick={() =>
                updateWaifu("interrupt_after", Math.max(0, interruptAfter - 1))
              }
            >
              <Minus className="h-4 w-4" />
            </Button>
            <Badge tone="secondary" className="w-24 justify-center tabular-nums text-xs">
              {interruptAfter === 0
                ? w.interruptImmediate
                : w.interruptMore.replace("{n}", String(interruptAfter))}
            </Badge>
            <Button
              ghost
              size="icon"
              aria-label={w.interruptMore.replace("{n}", String(INTERRUPT_AFTER_MAX))}
              disabled={interruptAfter >= INTERRUPT_AFTER_MAX}
              onClick={() =>
                updateWaifu(
                  "interrupt_after",
                  Math.min(INTERRUPT_AFTER_MAX, interruptAfter + 1),
                )
              }
            >
              <Plus className="h-4 w-4" />
            </Button>
          </div>
          <Hint>{w.interruptAfterHint}</Hint>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="px-4 py-3">
          <ToggleRow
            label={w.stripTrailingPunctuation}
            hint={w.stripTrailingPunctuationHint}
            checked={stripPunctuation}
            onChange={(value) => updateWaifu("strip_trailing_punctuation", value)}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="py-3 px-4">
          <CardTitle className="text-sm">{w.emojis}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 px-4 pb-4">
          <Hint>{w.emojisHint}</Hint>
          {emojiEnableKey && (
            <ToggleRow
              label={w.emojisEnabled}
              hint={w.emojisEnabledHint}
              checked={emojisEnabled}
              onChange={(value) => {
                setConfig((prev) =>
                  prev ? setNestedValue(prev, emojiEnableKey, value) : prev,
                );
                setDirty(true);
              }}
            />
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={newName}
              placeholder={w.emojiNamePlaceholder}
              aria-label={w.emojiName}
              onChange={(e) => setNewName(e.target.value)}
              className="w-40"
            />
            <Input
              value={newPath}
              placeholder={w.emojiPathPlaceholder}
              aria-label={w.emojiPath}
              onChange={(e) => setNewPath(e.target.value)}
              className="min-w-[12rem] flex-1"
            />
            <Button size="sm" prefix={<Plus />} onClick={addEmoji} disabled={!newName.trim()}>
              {w.emojiAdd}
            </Button>
          </div>

          {emojiEntriesList.length === 0 ? (
            <p className="py-4 text-center text-sm text-muted-foreground">
              {w.emojiEmpty}
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {emojiEntriesList.map((entry) => (
                <li
                  key={entry.name}
                  className="flex flex-wrap items-center gap-3 border border-border p-2"
                >
                  <EmojiThumb path={entry.path} fallback={w.emojiPreviewFailed} />
                  <span className="w-32 shrink-0 truncate font-mono text-xs">
                    {entry.name}
                  </span>
                  <Input
                    value={entry.path}
                    aria-label={`${w.emojiPath} — ${entry.name}`}
                    onChange={(e) =>
                      updateWaifu("emojis", upsertEmoji(emojis, entry.name, e.target.value))
                    }
                    className="min-w-[10rem] flex-1"
                  />
                  <Button
                    ghost
                    size="icon"
                    aria-label={`${w.emojiRemove} — ${entry.name}`}
                    onClick={() =>
                      updateWaifu("emojis", removeEmoji(emojis, entry.name))
                    }
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {showImmersive && (
        <Card>
          <CardContent className="px-4 py-3">
            <ToggleRow
              label={w.immersive}
              hint={w.immersiveHint}
              checked={immersive}
              onChange={(value) => updateWaifu("immersive", value)}
            />
          </CardContent>
        </Card>
      )}

      <EasterEgg label={w.easterEgg} />
    </div>
  );
}
