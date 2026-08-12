// IMPORTED DEP-TYPES
import type { Handle } from '@sveltejs/kit';
// IMPORTED DEP-MODULES
import { sequence } from '@sveltejs/kit/hooks';
// IMPORTED MODULES
import { THEME_BG, THEME_COOKIE } from '$lib/stores/settings';

// -- TYPES -- //

declare global {
	var __mtProcessGuards: boolean | undefined;
}

// -- CONSTANTS -- //

const DARK = ['dark', 'oled', 'contrast'];

// -- LIFECYCLES -- //

// PROCESS-LEVEL RESILIENCE. A STRAY ASYNC REJECTION — e.g. A DETACHED TRANSLATION JOB SAVING TO A PAGE
// THE USER DELETED MID-FLIGHT — MUST NOT TAKE THE WHOLE SERVER DOWN. NODE EXITS ON AN UNHANDLED REJECTION
// BY DEFAULT; LOG IT AND KEEP SERVING. REGISTERED ONCE (HMR-SAFE).
if (!globalThis.__mtProcessGuards) {
	globalThis.__mtProcessGuards = true;
	process.on('unhandledRejection', (reason) => console.error('[server] unhandled rejection (kept alive):', reason));
	process.on('uncaughtException', (err) => console.error('[server] uncaught exception (kept alive):', err));
}

// -- HANDLES -- //

// PRE-RENDER THE SAVED THEME ONTO <html> FROM THE COOKIE SO THERE'S NO FLASH ON LOAD
const themeHandle: Handle = async ({ event, resolve }) => {
	const theme = event.cookies.get(THEME_COOKIE) ?? 'sepia';
	const isDark = DARK.includes(theme);
	const bg = (THEME_BG as Record<string, string>)[theme] ?? THEME_BG.sepia;
	const htmlClass = isDark ? 'h-full dark' : 'h-full';
	return resolve(event, {
		// ALSO SEED THE MOBILE BROWSER-CHROME COLOR (theme-color META) FROM THE SAME THEME SO THE ADDRESS /
		// STATUS BAR MATCHES THE PAGE ON FIRST PAINT — THE CLIENT KEEPS IT IN SYNC ON THEME CHANGE.
		transformPageChunk: ({ html }) => html.replace('%THEME_CLASS%', htmlClass).replace('%THEME_COLOR%', bg),
	});
};

export const handle = sequence(themeHandle);
