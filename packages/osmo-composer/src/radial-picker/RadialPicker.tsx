import { type KeyboardEvent, type ReactNode, useCallback, useEffect, useId, useMemo, useReducer, useRef, useState } from "react";
import type { VoiceModel, VoiceSelection } from "../protocol";
import {
  optionTokenVars,
  CLOSE_GRACE_MS,
  FREEZE_AFTER_SNAP_MS,
  PREVIEW_HOVER_MS,
  PX_PER_SLOT,
  ringSpec,
  type RingKey,
  SNAP_IDLE_MS,
  SNAP_MS,
  easeOut,
  hitTest,
  itemAt,
  itemPresence,
  polar,
  slotAngle,
} from "./geometry";
import { RadialOption } from "./option";
import { type ProviderIcon, providerIcons as defaultIcons } from "./providerIcons";
import {
  type OptionLabel,
  PICKER_LABEL,
  RING_LABELS,
  effortLabel,
  modelLabel,
  providerLabel,
  voiceLabel,
} from "./ringLabels";
import { useReducedMotion } from "./useReducedMotion";

export { modelRingLabel } from "./ringLabels";
import { type Context, type PickerProvider, effortsOf, groupProviders, initialPicker, isFrozen, pickModel, reducePicker } from "./reducer";

export interface RadialPickerProps {
  registry: VoiceModel[];
  value: VoiceSelection;
  /** Fires once per commit (click, Enter or Space). Send `session.configure` from here. */
  onChange: (next: VoiceSelection) => void;
  orbSize?: number;
  /** Provider glyphs and labels keyed by `VoiceModel.provider`; unknown providers get a monogram. */
  providerIcons?: Record<string, ProviderIcon>;
  /** Orb face; defaults to a neutral glass sphere. Osmo passes its `GlassOrb`. */
  orb?: ReactNode;
  /** When set, a click on the orb calls this (e.g. start talking) and the dial opens on hover or focus only. */
  onOrbActivate?: () => void;
  /** Accessible name of the orb button when it has its own action. */
  orbLabel?: string;
  /** Called ~400 ms after a voice pill is hovered or focused; play a short sample. */
  onPreview?: (pick: { model: string; voice: string }) => void;
  /** Called when the hovered/focused voice changes or the dial closes. */
  onPreviewStop?: () => void;
  /** The voice currently previewing, for the pill's playing indicator. */
  previewing?: { model: string; voice: string };
  /** No previews right now (e.g. the agent is speaking). */
  previewDisabled?: boolean;
  /** Every provider the server knows (`GET /providers`); those without voice models show locked. */
  providers?: readonly PickerProvider[];
  /** A locked provider (not connected, or no voice models yet) was committed: offer to connect it. */
  onLockedProvider?: (provider: string) => void;
}

/** Dial canvas: R3 radius plus room for half a pill. */
const REACH = 300;
const KEY_ORDER: RingKey[] = ["providers", "models", "voices", "effort"];

interface Glide {
  ring: RingKey;
  from: number;
  to: number;
  start: number;
}


export function RadialPicker({
  registry,
  value,
  onChange,
  orbSize = 96,
  providerIcons = defaultIcons,
  orb,
  onPreview,
  onPreviewStop,
  previewing,
  previewDisabled = false,
  onOrbActivate,
  orbLabel,
  providers,
  onLockedProvider,
}: RadialPickerProps) {
  const groups = useMemo(() => groupProviders(registry, providers), [registry, providers]);
  const [state, dispatch] = useReducer(reducePicker, initialPicker);
  const reduced = useReducedMotion();
  const [glide, setGlide] = useState<{ ring: RingKey; value: number } | undefined>();
  const [hover, setHover] = useState<{ ring: RingKey; index: number } | undefined>();
  const [active, setActive] = useState<Partial<Record<RingKey, number>>>({});

  const ids = `rp${useId().replace(/:/g, "")}`;
  const root = useRef<HTMLDivElement>(null);
  const orbButton = useRef<HTMLButtonElement>(null);
  const stateRef = useRef(state);
  stateRef.current = state;
  const ctx: Context = { groups, value };
  const ctxRef = useRef(ctx);
  ctxRef.current = ctx;
  const timers = useRef<{ close?: number; snap?: number; thaw?: number; preview?: number; frame?: number }>({});
  const lastPointer = useRef<{ x: number; y: number } | undefined>();
  const wheelCarry = useRef(0);
  const drag = useRef<{ ring: RingKey; angle: number; id: number } | undefined>();

  const cx = REACH;
  const cy = REACH;
  const width = REACH * 2;

  const group = groups.find((g) => g.id === state.provider);
  const pathModel = group?.models.find((m) => m.id === state.model);
  const efforts = effortsOf(pathModel);
  const counts: Record<RingKey, number> = {
    providers: groups.length,
    models: group?.models.length ?? 0,
    voices: pathModel?.voices.length ?? 0,
    effort: efforts.length,
  };
  const countsRef = useRef(counts);
  countsRef.current = counts;
  const openRings: RingKey[] = KEY_ORDER.filter(
    (key) => (key === "providers" && state.depth >= 1) || (key === "models" && state.depth >= 2) || (key === "voices" && state.depth >= 3) || (key === "effort" && state.depth >= 3 && efforts.length > 0),
  );

  const offsetOf = (ring: RingKey) => (glide?.ring === ring ? glide.value : state.offsets[ring]);

  // ---------------------------------------------------------------- open / close

  const open = useCallback(() => {
    window.clearTimeout(timers.current.close);
    dispatch({ type: "open", ...ctxRef.current });
  }, []);

  const close = useCallback(() => {
    const t = timers.current;
    window.clearTimeout(t.close);
    window.clearTimeout(t.snap);
    window.clearTimeout(t.thaw);
    window.clearTimeout(t.preview);
    if (t.frame !== undefined) cancelAnimationFrame(t.frame);
    setGlide(undefined);
    setHover(undefined);
    setActive({});
    dispatch({ type: "close" });
    onPreviewStop?.();
  }, [onPreviewStop]);

  const scheduleClose = () => {
    if (timers.current.close !== undefined || stateRef.current.depth === 0) return;
    timers.current.close = window.setTimeout(() => {
      timers.current.close = undefined;
      close();
    }, CLOSE_GRACE_MS);
  };
  const cancelClose = () => {
    window.clearTimeout(timers.current.close);
    timers.current.close = undefined;
  };

  useEffect(() => {
    const t = timers.current;
    return () => {
      window.clearTimeout(t.close);
      window.clearTimeout(t.snap);
      window.clearTimeout(t.thaw);
      window.clearTimeout(t.preview);
      if (t.frame !== undefined) cancelAnimationFrame(t.frame);
    };
  }, []);

  // ---------------------------------------------------------------- hover

  const hoverItem = useCallback((ring: RingKey, index: number | undefined) => {
    setHover(index === undefined ? undefined : { ring, index });
    if (index === undefined) return;
    const now = performance.now();
    const { groups: gs } = ctxRef.current;
    const current = stateRef.current;
    if (ring === "providers") {
      const target = gs[index];
      if (target) dispatch({ type: "hoverProvider", provider: target.id, now, ...ctxRef.current });
    } else if (ring === "models") {
      const target = gs.find((g) => g.id === current.provider)?.models[index];
      if (target) dispatch({ type: "hoverModel", model: target.id, now, ...ctxRef.current });
    }
  }, []);

  const pointAt = useCallback(
    (x: number, y: number) => {
      const current = stateRef.current;
      const hit = hitTest(cx, cy, x, y);
      if (hit.ring === "none") {
        setHover(undefined);
        scheduleClose();
        return;
      }
      cancelClose();
      if (hit.ring === "orb") {
        if (current.depth === 0) open();
        setHover(undefined);
        return;
      }
      const ringOpen =
        (hit.ring === "providers" && current.depth >= 1) || (hit.ring === "models" && current.depth >= 2) || ((hit.ring === "voices" || hit.ring === "effort") && current.depth >= 3);
      if (!ringOpen) {
        setHover(undefined);
        return;
      }
      const ring = ringSpec(hit.ring, countsRef.current[hit.ring]);
      hoverItem(hit.ring, itemAt(ring, countsRef.current[hit.ring], current.offsets[hit.ring], hit.angle));
    },
    // scheduleClose / cancelClose only touch refs.
    [cx, cy, hoverItem, open],
  );

  const localPoint = (clientX: number, clientY: number) => {
    const box = root.current?.getBoundingClientRect();
    return box ? { x: clientX - box.left, y: clientY - box.top } : undefined;
  };

  // ---------------------------------------------------------------- scrolling

  const settleLater = useCallback(
    (ring: RingKey) => {
      const t = timers.current;
      const finish = () => {
        setGlide(undefined);
        dispatch({ type: "settle", ring, now: performance.now() });
        window.clearTimeout(t.thaw);
        t.thaw = window.setTimeout(() => {
          dispatch({ type: "thaw", now: performance.now() });
          // Resume hover where the pointer rests now.
          const point = lastPointer.current;
          if (point) window.setTimeout(() => pointAt(point.x, point.y), 0);
        }, FREEZE_AFTER_SNAP_MS + 5);
      };
      const from = stateRef.current.offsets[ring];
      dispatch({ type: "snap", ring, count: countsRef.current[ring] });
      const to = reducePicker(stateRef.current, { type: "snap", ring, count: countsRef.current[ring] }).offsets[ring];
      if (reduced || Math.abs(to - from) < 0.001) {
        finish();
        return;
      }
      const glideState: Glide = { ring, from, to, start: performance.now() };
      const frame = (now: number) => {
        const k = easeOut((now - glideState.start) / SNAP_MS);
        setGlide({ ring, value: glideState.from + (glideState.to - glideState.from) * k });
        if (k < 1) t.frame = requestAnimationFrame(frame);
        else {
          t.frame = undefined;
          finish();
        }
      };
      if (t.frame !== undefined) cancelAnimationFrame(t.frame);
      t.frame = requestAnimationFrame(frame);
    },
    [pointAt, reduced],
  );

  const scrollBy = useCallback(
    (ring: RingKey, slots: number) => {
      const t = timers.current;
      if (t.frame !== undefined) {
        cancelAnimationFrame(t.frame);
        t.frame = undefined;
        setGlide(undefined);
      }
      window.clearTimeout(t.snap);
      if (reduced) {
        // Whole slots only, no glide.
        wheelCarry.current += slots;
        const whole = wheelCarry.current >= 0.5 ? 1 : wheelCarry.current <= -0.5 ? -1 : 0;
        if (whole === 0) return;
        wheelCarry.current = 0;
        dispatch({ type: "scroll", ring, slots: whole, count: countsRef.current[ring] });
        settleLater(ring);
        return;
      }
      dispatch({ type: "scroll", ring, slots, count: countsRef.current[ring] });
      t.snap = window.setTimeout(() => settleLater(ring), SNAP_IDLE_MS);
    },
    [reduced, settleLater],
  );

  // Wheel needs a non-passive listener to stop the page from scrolling.
  useEffect(() => {
    const element = root.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      const current = stateRef.current;
      if (current.depth === 0) return;
      const point = localPoint(event.clientX, event.clientY);
      if (!point) return;
      const hit = hitTest(cx, cy, point.x, point.y);
      if (hit.ring === "none" || hit.ring === "orb") return;
      const isOpen =
        (hit.ring === "providers" && current.depth >= 1) || (hit.ring === "models" && current.depth >= 2) || ((hit.ring === "voices" || hit.ring === "effort") && current.depth >= 3);
      if (!isOpen) return;
      event.preventDefault();
      const scale = event.deltaMode === 1 ? 40 : event.deltaMode === 2 ? 400 : 1;
      scrollBy(hit.ring, ((event.deltaY + event.deltaX) * scale) / PX_PER_SLOT);
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [cx, cy, scrollBy]);

  // ---------------------------------------------------------------- previews

  const previewTarget =
    hover?.ring === "voices" && !reduced ? hover.index : document.activeElement?.id === `${ids}-voices` ? active.voices : undefined;
  const previewVoice = previewTarget === undefined ? undefined : pathModel?.voices[previewTarget];
  const previewKey = previewVoice && pathModel ? `${pathModel.id}/${previewVoice.id}` : undefined;
  useEffect(() => {
    const t = timers.current;
    window.clearTimeout(t.preview);
    if (!previewKey || previewDisabled || !onPreview) return;
    const [model, voice] = [previewKey.slice(0, previewKey.indexOf("/")), previewKey.slice(previewKey.indexOf("/") + 1)];
    t.preview = window.setTimeout(() => onPreview({ model, voice }), PREVIEW_HOVER_MS);
    return () => {
      window.clearTimeout(t.preview);
      onPreviewStop?.();
    };
  }, [previewKey, previewDisabled, onPreview, onPreviewStop]);

  // ---------------------------------------------------------------- commit

  const commit = (ring: RingKey, index: number) => {
    if (ring === "providers") {
      const target = groups[index];
      if (target?.locked) {
        close();
        onLockedProvider?.(target.id);
      } else if (target) dispatch({ type: "hoverProvider", provider: target.id, now: Number.POSITIVE_INFINITY, ...ctx });
      return;
    }
    if (ring === "models") {
      const model = group?.models[index];
      // A transcription-only model cannot answer, so it is shown but never becomes the selection.
      if (model && model.capabilities.speaks !== false) onChange(pickModel(model, value));
      return;
    }
    if (!pathModel) return;
    const base = pathModel.id === value.model ? value : pickModel(pathModel, value);
    if (ring === "voices") {
      const voice = pathModel.voices[index];
      if (voice) onChange({ ...base, voice: voice.id });
    } else {
      const effort = efforts[index];
      if (effort) onChange({ ...base, effort });
    }
  };

  // ---------------------------------------------------------------- keyboard

  const onRingKey = (ring: RingKey, event: KeyboardEvent<HTMLDivElement>) => {
    const count = counts[ring];
    const current = active[ring] ?? 0;
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      orbButton.current?.focus();
      return;
    }
    if (event.key === "ArrowLeft" || event.key === "ArrowRight" || event.key === "ArrowUp" || event.key === "ArrowDown") {
      event.preventDefault();
      const next = Math.min(count - 1, Math.max(0, current + (event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 1)));
      setActive((previous) => ({ ...previous, [ring]: next }));
      dispatch({ type: "reveal", ring, index: next, count });
      if (ring === "providers" || ring === "models") {
        // Keyboard moves count as hover for opening the next ring.
        const now = performance.now();
        if (!isFrozen(stateRef.current, ring, now)) hoverItem(ring, next);
        setHover(undefined);
      }
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      commit(ring, current);
    }
  };

  const onRingFocus = (ring: RingKey) => {
    if (active[ring] !== undefined) return;
    const selected = selectedIndex(ring);
    const start = selected >= 0 ? selected : Math.max(0, Math.ceil(state.offsets[ring]));
    setActive((previous) => ({ ...previous, [ring]: start }));
    if (ring === "providers" || ring === "models") hoverItem(ring, start);
    setHover(undefined);
  };

  const selectedIndex = (ring: RingKey): number => {
    if (ring === "providers") return groups.findIndex((g) => g.models.some((m) => m.id === value.model));
    if (ring === "models") return group?.models.findIndex((m) => m.id === value.model) ?? -1;
    if (!pathModel || pathModel.id !== value.model) return -1;
    if (ring === "voices") return pathModel.voices.findIndex((v) => v.id === value.voice);
    return value.effort === undefined ? -1 : efforts.indexOf(value.effort);
  };

  // ---------------------------------------------------------------- render


  const renderRing = (key: RingKey) => {
    const ring = ringSpec(key, counts[key]);
    const offset = offsetOf(key);
    const count = counts[key];
    const selected = selectedIndex(key);
    const labelFor = (index: number): OptionLabel => {
      if (key === "providers") return providerLabel(groups[index], providerIcons);
      if (key === "models") return modelLabel(group?.models[index]);
      if (key === "voices") {
        const v = pathModel?.voices[index];
        return voiceLabel(v, previewing !== undefined && previewing.model === pathModel?.id && previewing.voice === v?.id);
      }
      return effortLabel(efforts[index] ?? "low");
    };
    const items: ReactNode[] = [];
    for (let index = 0; index < count; index++) {
      const position = index - offset;
      const presence = itemPresence(ring, position);
      if (presence.opacity <= 0) continue;
      const { x, y } = polar(cx, cy, ring.radius, slotAngle(ring, position));
      const isSelected = index === selected;
      const isActive = active[key] === index && document.activeElement?.id === `${ids}-${key}`;
      const isHover = (hover?.ring === key && hover.index === index) || isActive;
      const isPath = (key === "providers" && groups[index]?.id === state.provider) || (key === "models" && group?.models[index]?.id === state.model);
      const label = labelFor(index);
      items.push(
        <div
          key={`${key}-${index}`}
          className="rp-slot"
          style={{ left: x, top: y, opacity: presence.opacity, transform: `translate(-50%, -50%) scale(${presence.scale})` }}
        >
          <RadialOption
            id={`${ids}-${key}-${index}`}
            ring={key}
            selected={isSelected}
            locked={
              (key === "providers" && groups[index]?.locked === true) ||
              (key === "models" && group?.models[index]?.capabilities.speaks === false)
            }
            title={label.tip === undefined ? label.title : undefined}
            label={label.tip === undefined ? label.title : `${label.text}, ${label.tip}`}
            state={isSelected ? "selected" : isHover || isPath ? "hover" : "idle"}
            delayMs={Math.max(0, Math.round(position)) * 20}
            onCommit={() => commit(key, index)}
          >
            {label.node}
          </RadialOption>
          {label.tip !== undefined && isHover && (
            <span className="rp-tip" role="tooltip" data-for={`${ids}-${key}-${index}`}>
              {label.tip}
            </span>
          )}
        </div>,
      );
    }
    const activeIndex = active[key];
    return (
      <div
        key={key}
        id={`${ids}-${key}`}
        className="rp-ring"
        data-ring={key}
        role="listbox"
        tabIndex={0}
        aria-label={RING_LABELS[key]}
        aria-orientation="horizontal"
        aria-activedescendant={activeIndex === undefined ? undefined : `${ids}-${key}-${activeIndex}`}
        onKeyDown={(event) => onRingKey(key, event)}
        onFocus={() => onRingFocus(key)}
      >
        {items}

      </div>
    );
  };

  return (
    <div
      ref={root}
      className="rp"
      data-open={state.depth > 0}
      data-depth={state.depth}
      data-reduced-motion={reduced}
      style={{ ...optionTokenVars(), width, height: cy + orbSize / 2 + 64 }}
      onPointerMove={(event) => {
        const point = localPoint(event.clientX, event.clientY);
        if (!point) return;
        lastPointer.current = point;
        const dragging = drag.current;
        if (dragging && dragging.id === event.pointerId) {
          const angle = (Math.atan2(cy - point.y, point.x - cx) * 180) / Math.PI;
          scrollBy(dragging.ring, (angle - dragging.angle) / ringSpec(dragging.ring, countsRef.current[dragging.ring]).step);
          dragging.angle = angle;
          return;
        }
        if (event.pointerType !== "touch") pointAt(point.x, point.y);
      }}
      onPointerDown={(event) => {
        const point = localPoint(event.clientX, event.clientY);
        if (!point || event.pointerType !== "touch") return;
        const hit = hitTest(cx, cy, point.x, point.y);
        if (hit.ring === "none" || hit.ring === "orb") return;
        drag.current = { ring: hit.ring, angle: hit.angle, id: event.pointerId };
        root.current?.setPointerCapture(event.pointerId);
      }}
      onPointerUp={() => {
        drag.current = undefined;
      }}
      onPointerLeave={() => {
        lastPointer.current = undefined;
        setHover(undefined);
        scheduleClose();
      }}
      onBlur={(event) => {
        const next = event.relatedTarget;
        if (!(next instanceof Node) || !root.current?.contains(next)) {
          if (!root.current?.matches(":hover")) close();
        }
      }}
    >
      <button
        ref={orbButton}
        type="button"
        className="rp-orb"
        style={{ left: cx, top: cy, width: orbSize, height: orbSize }}
        aria-expanded={state.depth > 0}
        aria-label={orbLabel === undefined ? PICKER_LABEL : `${orbLabel}. ${PICKER_LABEL}`}
        onFocus={(event) => {
          // Keyboard focus only: a tap or click focuses the orb too, and must not open the dial.
          if (focusVisible(event.currentTarget)) open();
        }}
        onClick={() => {
          if (onOrbActivate) onOrbActivate();
          else if (state.depth === 0) open();
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") close();
        }}
      >
        {orb ?? <span className="rp-glass" aria-hidden />}
      </button>
      {/* After the orb in DOM order so Tab goes orb → providers → models → voices → effort. */}
      <div className="rp-dial">{openRings.map(renderRing)}</div>
    </div>
  );
}

/** Whether `element` has keyboard (`:focus-visible`) focus; false where the selector is unsupported. */
export function focusVisible(element: Element): boolean {
  try {
    return element.matches(":focus-visible");
  } catch {
    return false;
  }
}
