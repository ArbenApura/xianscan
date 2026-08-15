<script lang="ts">
	// IMPORTED ENVS
	import { browser } from '$app/environment';
	// IMPORTED DEP-MODULES
	import { Toaster } from 'svelte-sonner';
	// IMPORTED MODULES
	import '../app.css';
	import { settings, THEME_CLASS, applyThemeClass } from '$lib/stores/settings';
	import type { LayoutData } from './$types';

	export let data: LayoutData;

	// SYNC STORE FROM SSR PREFERENCES (RUNS ON SERVER DURING SSR AND ON HYDRATION)
	$: if (data?.preferences) {
		settings.update((s) => ({
			...s,
			theme: data.preferences.theme ?? s.theme,
			readerViewMode: data.preferences.readerViewMode ?? s.readerViewMode,
			webtoonKind: data.preferences.webtoonKind ?? s.webtoonKind,
			webtoonWidth: data.preferences.webtoonWidth ?? s.webtoonWidth,
		}));
	}

	// KEEP THE DOCUMENT ROOT (dark CLASS, color-scheme, BG) IN SYNC WITH THE ACTIVE THEME
	$: if (browser) applyThemeClass($settings.theme);
</script>

<!-- APP ROOT — THEME SURFACE COLOURS APPLIED ONCE HERE; PAGES/PANELS INHERIT THEM -->
<div class={THEME_CLASS[$settings.theme] + ' min-h-screen'}>
	<slot />
</div>

<Toaster position="top-center" richColors />
