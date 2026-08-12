<script lang="ts">
	// IMPORTED ENVS
	import { browser } from '$app/environment';
	// IMPORTED DEP-MODULES
	import { Toaster } from 'svelte-sonner';
	// IMPORTED MODULES
	import '../app.css';
	import { settings, THEME_CLASS, applyThemeClass } from '$lib/stores/settings';

	// -- REACTIVE STATEMENTS -- //

	// KEEP THE DOCUMENT ROOT (dark CLASS, color-scheme, BG) IN SYNC WITH THE ACTIVE THEME
	$: if (browser) applyThemeClass($settings.theme);
</script>

<!-- APP ROOT — THEME SURFACE COLOURS APPLIED ONCE HERE; PAGES/PANELS INHERIT THEM -->
<div class={THEME_CLASS[$settings.theme] + ' min-h-screen'}>
	<slot />
</div>

<Toaster position="top-center" richColors />
