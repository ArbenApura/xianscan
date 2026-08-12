// IMPORTED ENVS
import { browser } from '$app/environment';
// IMPORTED DEP-MODULES
import { writable } from 'svelte/store';
// IMPORTED MODULES
import { DEFAULT_SOURCE_LANG, DEFAULT_TARGET_LANG } from '$lib/languages';

// -- TYPES -- //

export type Theme = 'light' | 'sepia' | 'dark' | 'oled' | 'contrast';

export interface AppSettings {
	version: number;
	theme: Theme;
	// THE GLOBAL DEEPSEEK MODEL THE TRANSLATE PIPELINE USES (flash = fast/cheap, pro = best). SENT WITH
	// EVERY TRANSLATE REQUEST; THE SERVER VALIDATES IT AGAINST ITS ALLOWLIST (src/lib/server/deepseek).
	model: string;
	// DEFAULT TRANSLATION DIRECTION FOR NEWLY CREATED BOOKS (PER-BOOK OVERRIDES AT CREATION)
	sourceLang: string;
	targetLang: string;
	// WATERMARK ERASE & DETECTION CONTROLS
	watermarkRemoval: boolean;
	customWatermarks: string;
	// PERSISTENT READER CONFIGURATIONS
	readerViewMode: 'reader' | 'grid' | 'compare';
	webtoonKind: 'output' | 'original';
	webtoonWidth: 'sm' | 'md' | 'lg';
}

// CLIENT-FACING MODEL CHOICES FOR THE GLOBAL PICKER. THE IDS MIRROR THE SERVER DEFAULTS IN
// $lib/server/deepseek (resolveModel VALIDATES WHATEVER THE CLIENT SENDS, SO A STALE ID IS SAFE).
export const TRANSLATION_MODELS: { id: string; label: string; blurb: string }[] = [
	{ id: 'deepseek-v4-flash', label: 'Flash', blurb: 'Fast & economical — great for everyday use' },
	{ id: 'deepseek-v4-pro', label: 'Pro', blurb: 'Higher-quality prose — slower, costs more' },
];

// -- CONSTANTS -- //

// BUMP version WHEN DEFAULTS CHANGE — TRIGGERS A ONE-TIME MIGRATION OF SAVED SETTINGS
export const DEFAULTS: AppSettings = {
	version: 3,
	theme: 'sepia',
	model: 'deepseek-v4-flash',
	sourceLang: DEFAULT_SOURCE_LANG,
	targetLang: DEFAULT_TARGET_LANG,
	watermarkRemoval: false,
	customWatermarks: '',
	readerViewMode: 'reader',
	webtoonKind: 'output',
	webtoonWidth: 'md',
};

const KEY = 'manua:settings';

// COOKIE LETS THE SERVER RENDER THE CORRECT THEME ON FIRST PAINT (NO FLICKER)
export const THEME_COOKIE = 'mt_theme';

const DARK_THEMES: Theme[] = ['dark', 'oled', 'contrast'];

// SINGLE SOURCE OF TRUTH FOR THEME SURFACE COLOURS — APPLIED APP-WIDE AT THE LAYOUT ROOT
// WARM INK ON PAPER (light/sepia) AND WARM OFF-WHITE ON WARM LACQUER / TRUE BLACK (dark/oled/contrast).
export const THEME_CLASS: Record<Theme, string> = {
	light: 'bg-[#fbfaf7] text-[#2b2320]',
	sepia: 'bg-[#f4ecd8] text-[#5b4636]',
	dark: 'bg-[#13100c] text-[#d8cfc2]',
	oled: 'bg-black text-[#d8cfc2]',
	contrast: 'bg-black text-white',
};

// ROOT BACKGROUND PER THEME — KEEPS BROWSER CHROME, SCROLLBARS, AND OVERSCROLL IN SYNC
export const THEME_BG: Record<Theme, string> = {
	light: '#fbfaf7',
	sepia: '#f4ecd8',
	dark: '#13100c',
	oled: '#000000',
	contrast: '#000000',
};

// OPAQUE ELEVATED SURFACE FOR OVERLAYS (MODALS, BOTTOM SHEETS, DRAWERS). UNLIKE PAGE CARDS — WHICH USE
// TRANSLUCENT TINTS THAT LAYER OVER THE THEME BG — A FLOATING PANEL MUST BE OPAQUE. SO EACH THEME GETS ITS
// OWN SOLID PANEL COLOUR THAT SITS ONE STEP ABOVE ITS PAGE BACKGROUND, PLUS A FOREGROUND TUNED FOR CONTRAST.
export const THEME_PANEL: Record<Theme, string> = {
	light: 'bg-white text-[#2b2320]',
	sepia: 'bg-[#fbf6ea] text-[#5b4636]',
	dark: 'bg-[#211c15] text-[#e6ded2]',
	oled: 'bg-[#0c0b0a] text-[#e6ded2]',
	contrast: 'bg-black text-white',
};

// POPOVERS / DROPDOWN MENUS — ONE ELEVATION HIGHER THAN A PANEL (THEY OFTEN OPEN ON TOP OF ONE)
export const THEME_POPOVER: Record<Theme, string> = {
	light: 'bg-white text-[#2b2320]',
	sepia: 'bg-[#fdf9f0] text-[#5b4636]',
	dark: 'bg-[#2a231a] text-[#e6ded2]',
	oled: 'bg-[#161310] text-[#e6ded2]',
	contrast: 'bg-[#050505] text-white',
};

// BORDER FOR ELEVATED OVERLAYS — A SOFT TINT ON MOST THEMES, A WARM HAIRLINE ON SEPIA, AND A BRIGHT,
// CRISP EDGE ON contrast (WHERE THE WHOLE POINT IS MAXIMUM SEPARATION).
export const THEME_PANEL_BORDER: Record<Theme, string> = {
	light: 'border-black/10',
	sepia: 'border-[#e2d4b5]',
	dark: 'border-white/10',
	oled: 'border-white/[0.12]',
	contrast: 'border-white/40',
};

// TRANSLUCENT CHROME BARS (STICKY HEADERS) — SIT OVER backdrop-blur AND THE THEME BG.
export const THEME_BAR: Record<Theme, string> = {
	light: 'bg-white/70',
	sepia: 'bg-[#f4ecd8]/72',
	dark: 'bg-[#13100c]/70',
	oled: 'bg-black/55',
	contrast: 'bg-black/80',
};

// BRAND PALETTE — COMPLETE LITERAL CLASS STRINGS SO TAILWIND'S CONTENT SCANNER PICKS THEM UP FROM THIS
// .ts FILE. CINNABAR 朱砂 — THE PRIMARY ACTION ACCENT (BUTTONS, LINKS, SELECTED STATES, PROGRESS).
export const ACCENT_SOLID = 'bg-[#b23a2e] text-white hover:bg-[#c0392b]';
// CINNABAR TEXT / ICON ACCENT — ONE STEP BRIGHTER ON THE DARK GROUP FOR CONTRAST.
export const ACCENT_TEXT = 'text-[#b23a2e] dark:text-[#e08a63]';
// CINNABAR TINTED FILL FOR ACTIVE / SELECTED PILLS.
export const ACCENT_SOFT = 'bg-[#b23a2e]/12 text-[#b23a2e] dark:text-[#e08a63]';
// CINNABAR FOCUS RING.
export const ACCENT_RING = 'focus:ring-2 focus:ring-[#b23a2e]/40';
// JADE 青 — SUCCESS / "READ" / CONSISTENT STATE.
export const JADE_TEXT = 'text-[#4f7a64] dark:text-[#83b39a]';
export const JADE_SOFT = 'bg-[#5b8a72]/14 text-[#4f7a64] dark:text-[#83b39a]';
// AGED GOLD 赤金 — PREMIUM (PRO MODEL).
export const GOLD_TEXT = 'text-[#a97f28] dark:text-[#d8b15a]';
export const GOLD_SOFT = 'bg-[#c9a24b]/16 text-[#a97f28] dark:text-[#d8b15a]';

// -- STORES -- //

export const settings = createSettings();

// -- FUNCTIONS -- //

export function isDarkTheme(theme: Theme): boolean {
	return DARK_THEMES.includes(theme);
}

// APPLY THE THEME AT THE DOCUMENT ROOT: dark CLASS, color-scheme, AND ROOT BACKGROUND
export function applyThemeClass(theme: Theme): void {
	if (!browser) return;
	const isDark = DARK_THEMES.includes(theme);
	const root = document.documentElement;
	root.classList.toggle('dark', isDark);
	root.style.colorScheme = isDark ? 'dark' : 'light';
	root.style.backgroundColor = THEME_BG[theme];
	// KEEP THE MOBILE BROWSER CHROME (ADDRESS / STATUS BAR) IN SYNC WITH THE ACTIVE THEME — THE SSR HOOK
	// SEEDS THIS META ON FIRST PAINT; THIS UPDATES IT WHENEVER THE USER SWITCHES THEMES.
	document.querySelector('meta[name="theme-color"]')?.setAttribute('content', THEME_BG[theme]);
}

export function resetSettings() {
	settings.set({ ...DEFAULTS });
}

// MERGE A PARSED OBJECT ONTO DEFAULTS, KEEPING ONLY KNOWN KEYS WHOSE VALUE TYPE MATCHES THE DEFAULT —
// SO STALE/REMOVED KEYS AND TYPE-CORRUPTED VALUES ARE DROPPED WHILE VALID PREFERENCES SURVIVE.
function mergeKnown(parsed: unknown): AppSettings {
	const out = { ...DEFAULTS };
	if (parsed && typeof parsed === 'object') {
		for (const k of Object.keys(DEFAULTS) as (keyof AppSettings)[]) {
			const v = (parsed as Record<string, unknown>)[k];
			if (v !== undefined && typeof v === typeof DEFAULTS[k]) (out as Record<string, unknown>)[k] = v;
		}
	}
	if (!['reader', 'grid', 'compare'].includes(out.readerViewMode)) out.readerViewMode = 'reader';
	if (!['output', 'original'].includes(out.webtoonKind)) out.webtoonKind = 'output';
	if (!['sm', 'md', 'lg'].includes(out.webtoonWidth)) out.webtoonWidth = 'md';
	out.version = DEFAULTS.version;
	return out;
}

function load(): AppSettings {
	if (!browser) return { ...DEFAULTS };
	try {
		const raw = localStorage.getItem(KEY);
		if (raw) {
			const parsed = JSON.parse(raw);
			// MERGE THE USER'S SAVED VALUES *FORWARD* ONTO THE CURRENT DEFAULTS RATHER THAN DISCARDING THEM
			// ON A version BUMP — NEW KEYS COME FROM DEFAULTS; KNOWN KEYS KEEP THE SAVED VALUE (TYPE-CHECKED).
			return mergeKnown(parsed);
		}
	} catch {
		// IGNORE CORRUPT STATE
	}
	return { ...DEFAULTS };
}

function createSettings() {
	const store = writable<AppSettings>(load());
	if (browser) {
		let prevTheme: Theme | null = null;
		store.subscribe((s) => {
			try {
				localStorage.setItem(KEY, JSON.stringify(s));
				// MIRROR THE THEME TO A COOKIE SO SSR CAN PRE-RENDER IT
				document.cookie = `${THEME_COOKIE}=${s.theme}; path=/; max-age=31536000; samesite=lax`;
			} catch {
				// IGNORE STORAGE ERRORS (PRIVATE MODE / QUOTA)
			}
			// ONLY TOUCH THE DOCUMENT ROOT WHEN THE THEME ACTUALLY CHANGED — OTHER EDITS (THE COMMON CASE)
			// SHOULDN'T REWRITE classList/colorScheme/backgroundColor ON EVERY KEYSTROKE.
			if (s.theme !== prevTheme) {
				prevTheme = s.theme;
				applyThemeClass(s.theme);
			}
		});
	}
	return store;
}
