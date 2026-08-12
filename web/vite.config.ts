import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [sveltekit()],
	// FORCE VITE TO PROCESS lucide-svelte'S .svelte ICON FILES INSTEAD OF EXTERNALIZING THEM,
	// WHICH NODE'S NATIVE ESM LOADER REJECTS (ERR_UNKNOWN_FILE_EXTENSION ".svelte") UNDER SSR.
	ssr: {
		noExternal: ['lucide-svelte'],
	},
});
