"use client";

// Adapted from chatcn's settings panel (https://chatcn.dev). Its colour variables are defined in app/globals.css and point at this
// app's tokens, so it follows light/dark and Settings → Appearance; the original injected its own palette instead.

import { Switch } from "@base-ui/react/switch";
import { Tabs } from "@base-ui/react/tabs";
import * as React from "react";
import { cn } from "@/lib/utils";

interface SettingsRowContextValue { labelId: string; descriptionId?: string; disabled?: boolean }
const SettingsRowContext = React.createContext<SettingsRowContextValue | null>(null);

/** Label and description ids of the nearest `SettingsRow`, so a control can name itself. */
export function useSettingsRow() {
  return React.useContext(SettingsRowContext);
}

/* ------------------------------------------------------------------ Switch */
export type SettingsSwitchProps = React.ComponentProps<typeof Switch.Root>;

/** On/off. Inside a `SettingsRow` it takes the row's label and description. */
export function SettingsSwitch({ className, disabled, ...props }: SettingsSwitchProps) {
  const row = useSettingsRow();
  return (
    <Switch.Root
      aria-labelledby={props["aria-label"] ? undefined : row?.labelId}
      aria-describedby={row?.descriptionId}
      disabled={disabled ?? row?.disabled}
      data-slot="settings-switch"
      className={cn(
        "chatcn-focus relative inline-flex h-[1.125rem] w-8 shrink-0 cursor-pointer items-center rounded-full p-0.5",
        "bg-(color:--chatcn-fill-active) data-[checked]:bg-(color:--chatcn-accent)",
        "ease-(--chatcn-ease-standard) transition-[background-color] duration-150",
        "data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50",
        "pointer-coarse:after:absolute pointer-coarse:after:-inset-3 pointer-coarse:after:content-['']",
        className,
      )}
      {...props}
    >
      <Switch.Thumb
        className={cn(
          "bg-(color:--chatcn-card) shadow-(--chatcn-shadow-xs) block size-3.5 rounded-full",
          "ease-(--chatcn-ease-out) transition-[translate,background-color] duration-150 motion-reduce:transition-none",
          "data-[checked]:bg-(color:--chatcn-accent-fg) data-[checked]:translate-x-3.5",
        )}
      />
    </Switch.Root>
  );
}

/* ------------------------------------------------------------------ Segmented */
export interface SettingsSegmentedOption { value: string; label: React.ReactNode; icon?: React.ReactNode; iconOnly?: boolean; disabled?: boolean }

export interface SettingsSegmentedProps extends Omit<React.ComponentProps<"div">, "onChange" | "defaultValue"> {
  options: SettingsSegmentedOption[];
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  disabled?: boolean;
  size?: "xs" | "sm";
}

/** Two to four exclusive choices in one track. Native radio inputs underneath: Tab enters, arrow keys move and select. */
export function SettingsSegmented({ options, value: valueProp, defaultValue, onValueChange, disabled: disabledProp, size = "sm", className, ...props }: SettingsSegmentedProps) {
  const row = useSettingsRow();
  const disabled = disabledProp ?? row?.disabled;
  const [valueState, setValueState] = React.useState(defaultValue ?? options[0]?.value ?? "");
  const value = valueProp ?? valueState;
  const name = React.useId();
  return (
    <div
      role="radiogroup"
      aria-labelledby={props["aria-label"] ? undefined : row?.labelId}
      aria-describedby={row?.descriptionId}
      aria-disabled={disabled || undefined}
      data-slot="settings-segmented"
      className={cn("bg-(color:--chatcn-fill) inline-flex shrink-0 items-center gap-0.5 p-0.5", size === "xs" ? "rounded-(--chatcn-radius-sm)" : "rounded-(--chatcn-radius-md)", disabled && "opacity-50", className)}
      {...props}
    >
      {options.map((option) => {
        const checked = option.value === value;
        return (
          <label
            key={option.value}
            data-checked={checked ? "" : undefined}
            className={cn(
              "relative inline-flex cursor-pointer items-center justify-center gap-1.5 font-medium whitespace-nowrap select-none",
              size === "xs" ? "text-xs rounded-(--chatcn-radius-xs) h-6 px-2 [&_svg]:size-3.5" : "text-sm rounded-(--chatcn-radius-sm) h-7 px-2.5 [&_svg]:size-4",
              option.iconOnly && (size === "xs" ? "w-6 px-0" : "w-8 px-0"),
              "text-(color:--chatcn-fg-muted) hover:text-(color:--chatcn-fg) transition-colors duration-150",
              "data-[checked]:bg-(color:--chatcn-card) data-[checked]:text-(color:--chatcn-fg) data-[checked]:shadow-(--chatcn-shadow-xs) data-[checked]:transition-none",
              "has-[:focus-visible]:outline-(color:--acc) has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-1",
              "has-[:disabled]:pointer-events-none has-[:disabled]:opacity-50 pointer-coarse:min-h-11",
            )}
          >
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={checked}
              disabled={disabled || option.disabled}
              aria-label={option.iconOnly && typeof option.label === "string" ? option.label : undefined}
              onChange={() => { if (valueProp === undefined) setValueState(option.value); onValueChange?.(option.value); }}
              className="sr-only"
            />
            {option.icon ? <span aria-hidden="true" className="grid place-items-center">{option.icon}</span> : null}
            {option.iconOnly ? null : <span>{option.label}</span>}
          </label>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ Row */
export interface SettingsRowProps extends Omit<React.ComponentProps<"div">, "title"> {
  label: React.ReactNode;
  description?: React.ReactNode;
  /** Put the control under the text, full width: for inputs, lists and wide pickers. */
  stacked?: boolean;
  disabled?: boolean;
  note?: React.ReactNode;
}

/** One setting: label and description on the start side, the control on the end side. */
export function SettingsRow({ label, description, stacked = false, disabled = false, note, className, children, ...props }: SettingsRowProps) {
  const labelId = React.useId();
  const descriptionId = React.useId();
  const value = React.useMemo(() => ({ labelId, descriptionId: description ? descriptionId : undefined, disabled }), [labelId, descriptionId, description, disabled]);
  return (
    <SettingsRowContext.Provider value={value}>
      <div data-slot="settings-row" data-disabled={disabled ? "" : undefined}
        className={cn("flex min-h-14 flex-wrap items-center gap-x-6 gap-y-3 py-3.5", stacked && "flex-col items-stretch", className)} {...props}>
        <div className="flex min-w-[min(100%,16rem)] flex-1 flex-col gap-0.5">
          <span id={labelId} className={cn("text-sm font-medium", disabled ? "text-(color:--chatcn-fg-muted)" : "text-(color:--chatcn-fg)")}>{label}</span>
          {description ? <span id={descriptionId} className="text-sm text-pretty text-(color:--chatcn-fg-muted)">{description}</span> : null}
          {note ? <span className="pt-1 text-xs text-(color:--chatcn-fg-muted)">{note}</span> : null}
        </div>
        {children !== undefined && children !== null ? (
          <div data-slot="settings-row-control" className={cn("flex min-w-0 shrink-0 items-center gap-2", stacked && "w-full")}>{children}</div>
        ) : null}
      </div>
    </SettingsRowContext.Provider>
  );
}

/* ------------------------------------------------------------------ Panel */
export interface SettingsPanelProps extends Omit<React.ComponentProps<typeof Tabs.Root>, "value" | "defaultValue" | "onValueChange"> {
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
}

const STACK_BELOW = 672; // below this width the nav sits above the pane

/** A sections nav and one pane. At 42rem and wider (a container query) the nav is a column; narrower, a scrolling row on top. */
export function SettingsPanel({ value, defaultValue, onValueChange, className, children, ...props }: SettingsPanelProps) {
  const ref = React.useRef<HTMLDivElement>(null);
  const [stacked, setStacked] = React.useState(false);
  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => { if (entry) setStacked(entry.contentRect.width < STACK_BELOW); });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return (
    <Tabs.Root ref={ref} value={value} defaultValue={defaultValue} onValueChange={(next) => onValueChange?.(String(next))}
      orientation={stacked ? "horizontal" : "vertical"} data-slot="settings-panel"
      className={cn("text-sm text-(color:--chatcn-fg) @container/settings flex min-h-0", className)} {...props}>
      <div className="flex min-h-0 w-full min-w-0 flex-col @2xl/settings:flex-row">{children}</div>
    </Tabs.Root>
  );
}

/* ------------------------------------------------------------------ Pane */
export interface SettingsPaneProps extends Omit<React.ComponentProps<typeof Tabs.Panel>, "value" | "title"> {
  value: string;
  title?: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  contentClassName?: string;
}

export function SettingsPane({ value, title, description, actions, className, contentClassName, children, ...props }: SettingsPaneProps) {
  return (
    <Tabs.Panel value={value} data-slot="settings-pane"
      className={cn("chatcn-scroll min-h-0 min-w-0 flex-1 overflow-y-auto outline-none focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-(color:--acc)", className)} {...props}>
      <div className={cn("mx-auto flex w-full max-w-2xl flex-col gap-8 px-4 pt-5 pb-8 @2xl/settings:px-8 @2xl/settings:pt-7 @2xl/settings:pb-10", contentClassName)}>
        {title || description || actions ? (
          <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              {title ? <h2 className="text-xl font-medium tracking-tight text-balance text-(color:--chatcn-fg)">{title}</h2> : null}
              {description ? <p className="max-w-prose text-sm text-pretty text-(color:--chatcn-fg-muted)">{description}</p> : null}
            </div>
            {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
          </header>
        ) : null}
        {children}
      </div>
    </Tabs.Panel>
  );
}

/** A hairline between groups of sections in the column layout. Hidden in the row layout. */
export function SettingsNavSeparator({ className, ...props }: React.ComponentProps<"div">) {
  return <div role="presentation" data-slot="settings-nav-separator" className={cn("bg-(color:--chatcn-border) mx-2 my-2 hidden h-px @2xl/settings:block", className)} {...props} />;
}

export interface SettingsNavItemProps extends Omit<React.ComponentProps<typeof Tabs.Tab>, "value"> {
  value: string;
  icon?: React.ReactNode;
  meta?: React.ReactNode;
}

export function SettingsNavItem({ value, icon, meta, className, children, ...props }: SettingsNavItemProps) {
  return (
    <Tabs.Tab value={value} data-slot="settings-nav-item"
      className={cn(
        "group/nav-item chatcn-focus rounded-(--chatcn-radius-md) flex h-8 shrink-0 items-center gap-2.5 px-2.5 text-start whitespace-nowrap select-none",
        "text-sm text-(color:--chatcn-fg-muted) @2xl/settings:px-2",
        "hover:bg-(color:--chatcn-fill-hover) hover:text-(color:--chatcn-fg) transition-colors duration-150",
        "data-[active]:bg-(color:--chatcn-fill) data-[active]:text-(color:--chatcn-fg) data-[active]:transition-none",
        "disabled:pointer-events-none disabled:opacity-50 pointer-coarse:h-11",
        className,
      )} {...props}>
      {icon ? <span aria-hidden="true" className="grid size-4 shrink-0 place-items-center text-(color:--chatcn-fg-subtle) group-hover/nav-item:text-(color:--chatcn-fg-muted) group-data-[active]/nav-item:text-(color:--chatcn-fg) [&_svg]:size-4">{icon}</span> : null}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {meta !== undefined && meta !== null ? <span className="hidden shrink-0 text-xs tabular-nums text-(color:--chatcn-fg-subtle) @2xl/settings:inline">{meta}</span> : null}
    </Tabs.Tab>
  );
}

/* ------------------------------------------------------------------ Nav */
export interface SettingsNavProps extends Omit<React.ComponentProps<"nav">, "title"> {
  title?: React.ReactNode;
  footer?: React.ReactNode;
  label?: string;
}

export function SettingsNav({ title, footer, label = "Settings sections", className, children, ...props }: SettingsNavProps) {
  const listRef = React.useRef<HTMLDivElement>(null);
  const [edges, setEdges] = React.useState({ start: false, end: false });
  const measure = React.useCallback(() => {
    const list = listRef.current;
    if (!list) return;
    const row = getComputedStyle(list).overflowX === "auto";
    const pos = Math.abs(list.scrollLeft);
    const next = { start: row && pos > 1, end: row && pos + list.clientWidth < list.scrollWidth - 1 };
    setEdges((prev) => (prev.start === next.start && prev.end === next.end ? prev : next));
  }, []);
  React.useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return;
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(list);
    return () => observer.disconnect();
  }, [measure]);
  return (
    <nav aria-label={label} data-slot="settings-nav"
      className={cn("border-(color:--chatcn-border) flex shrink-0 flex-col border-b @2xl/settings:w-56 @2xl/settings:border-e @2xl/settings:border-b-0", className)} {...props}>
      {title ? <h2 className="px-4 pt-4 pb-1 text-base font-medium tracking-tight text-(color:--chatcn-fg) @2xl/settings:px-5 @2xl/settings:pt-5 @2xl/settings:pb-3">{title}</h2> : null}
      <Tabs.List ref={listRef} onScroll={measure} data-slot="settings-nav-list" data-scroll-start={edges.start ? "" : undefined} data-scroll-end={edges.end ? "" : undefined}
        className={cn(
          "relative flex [scrollbar-width:none] gap-1 overflow-x-auto px-3 py-2 [&::-webkit-scrollbar]:hidden",
          "[--fade-end:0px] [--fade-start:0px] data-[scroll-end]:[--fade-end:32px] data-[scroll-start]:[--fade-start:32px]",
          "[mask-image:linear-gradient(to_right,transparent,black_var(--fade-start),black_calc(100%-var(--fade-end)),transparent)]",
          "@2xl/settings:[mask-image:none] @2xl/settings:flex-col @2xl/settings:gap-px @2xl/settings:overflow-x-visible @2xl/settings:px-3 @2xl/settings:py-0",
        )}>
        {children}
      </Tabs.List>
      {footer ? <div data-slot="settings-nav-footer" className="mt-auto hidden px-3 pt-3 pb-3 @2xl/settings:block">{footer}</div> : null}
    </nav>
  );
}

/* ------------------------------------------------------------------ Group */
export interface SettingsGroupProps extends Omit<React.ComponentProps<"section">, "title"> {
  title?: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
}

/** A titled set of rows separated by hairlines. No card: the panel is already a surface. */
export function SettingsGroup({ title, description, action, className, children, ...props }: SettingsGroupProps) {
  const id = React.useId();
  return (
    <section data-slot="settings-group" aria-labelledby={title ? id : undefined} className={cn("flex flex-col gap-2", className)} {...props}>
      {title || action ? (
        <div className="flex min-h-7 items-end justify-between gap-3">
          <div className="flex min-w-0 flex-col gap-0.5">
            {title ? <h3 id={id} className="text-sm font-medium text-(color:--chatcn-fg)">{title}</h3> : null}
            {description ? <p className="text-xs text-pretty text-(color:--chatcn-fg-subtle)">{description}</p> : null}
          </div>
          {action}
        </div>
      ) : null}
      <div data-slot="settings-group-rows" className="flex flex-col divide-y divide-(color:--chatcn-border) border-t border-(color:--chatcn-border)">{children}</div>
    </section>
  );
}

export default SettingsPanel;
