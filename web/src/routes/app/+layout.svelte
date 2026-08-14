<script lang="ts">
	// IMPORTED DEP-COMPONENTS
	import { page } from '$app/stores';
	import { toast } from 'svelte-sonner';
	// IMPORTED MODULES
	import { ripple } from '$lib/actions/ripple';
	import {
		settings,
		THEME_CLASS,
		THEME_BAR,
		TRANSLATION_MODELS,
		type Theme,
	} from '$lib/stores/settings';
	import { activeTranslatingChapters } from '$lib/stores/job-tracker';
	// IMPORTED ICONS
	import BookOpen from 'lucide-svelte/icons/book-open';
	import Languages from 'lucide-svelte/icons/languages';
	import Settings from 'lucide-svelte/icons/settings';
	import Sun from 'lucide-svelte/icons/sun';
	import Moon from 'lucide-svelte/icons/moon';
	import Coffee from 'lucide-svelte/icons/coffee';
	import Contrast from 'lucide-svelte/icons/contrast';
	import Check from 'lucide-svelte/icons/check';
	import Cpu from 'lucide-svelte/icons/cpu';
	import Sparkles from 'lucide-svelte/icons/sparkles';

	// IMPORTED UI COMPONENTS
	import Modal from '$lib/components/ui/Modal.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import LanguagePicker from '$lib/components/ui/LanguagePicker.svelte';

	// -- STATES -- //
	let settingsOpen = false;

	const THEMES: { id: Theme; label: string; dot: string }[] = [
		{ id: 'light', label: 'Light', dot: 'border-slate-300 bg-[#fbfaf7]' },
		{ id: 'sepia', label: 'Sepia', dot: 'border-[#d4c3a3] bg-[#f4ecd8]' },
		{ id: 'dark', label: 'Dark', dot: 'border-neutral-700 bg-[#13100c]' },
		{ id: 'oled', label: 'OLED', dot: 'border-neutral-800 bg-black' },
		{ id: 'contrast', label: 'Contrast', dot: 'border-white bg-black' },
	];

	const THEME_ORDER: Theme[] = ['light', 'sepia', 'dark', 'oled', 'contrast'];

	function setTheme(t: Theme | string) {
		settings.update((s) => ({ ...s, theme: t as Theme }));
		const label = THEMES.find((item) => item.id === t)?.label || t;
		toast.success(`Theme updated to ${label}`);
	}

	function cycleTheme() {
		const currentIndex = THEME_ORDER.indexOf($settings.theme);
		const nextIndex = (currentIndex + 1) % THEME_ORDER.length;
		setTheme(THEME_ORDER[nextIndex]);
	}

	function setModel(m: string) {
		settings.update((s) => ({ ...s, model: m }));
		toast.success(`Model set to ${m === 'deepseek-v4-pro' ? 'DeepSeek Pro' : 'DeepSeek Flash'}`);
	}

	function updateSourceLang(lang: string) {
		settings.update((s) => ({ ...s, sourceLang: lang }));
	}

	function updateTargetLang(lang: string) {
		settings.update((s) => ({ ...s, targetLang: lang }));
	}

	$: activePath = $page.url.pathname as string;
	$: isGlossaryActive = activePath.startsWith('/app/glossary');
	$: isLibraryActive = !isGlossaryActive && (activePath === '/app/' || activePath === '/app' || activePath.startsWith('/app/books'));
</script>

<!-- APP SHELL — THEMED SURFACE + TOP NAV -->
<div class={THEME_CLASS[$settings.theme] + ' min-h-screen font-sans transition-colors duration-200'}>
	<!-- SLEEK & TACTILE TOP BAR -->
	<header
		class={`sticky top-0 z-40 border-b border-black/[0.07] backdrop-blur-md transition-colors duration-200 dark:border-white/[0.07] ${THEME_BAR[$settings.theme]}`}
	>
		<nav class="mx-auto flex w-full max-w-6xl items-center justify-between gap-4 px-4 py-2.5 sm:px-6">
			<!-- LEFT: BRAND & MAIN NAVIGATION TABS -->
			<div class="flex items-center gap-3 sm:gap-5">
				<!-- BRAND LOGO -->
				<a
					href="/app/"
					class="group flex items-center gap-2.5 text-base font-bold tracking-tight text-current transition opacity-90 hover:opacity-100"
				>
					<img
						src="/favicon.svg"
						alt="Manhua Translator"
						class="h-7 w-7 rounded-lg shadow-xs object-contain transition-transform duration-300 group-hover:scale-105"
					/>
					<span class="font-bold tracking-tight">Manhua <span class="text-[#b23a2e] dark:text-[#e08a63]">Translator</span></span>
				</a>

				<div class="h-4 w-px bg-black/10 dark:bg-white/10 hidden sm:block"></div>

				<!-- SEGMENTED NAVIGATION TABS (ACTIVE STATES) -->
				<div class="flex items-center gap-1 rounded-xl border border-black/[0.06] bg-black/[0.04] p-1 dark:border-white/[0.06] dark:bg-white/[0.04]">
					<a
						href="/app/"
						class={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition-all duration-200 ${
							isLibraryActive
								? 'bg-white font-bold text-black shadow-xs dark:bg-[#221e1a] dark:text-white'
								: 'font-medium opacity-65 hover:opacity-100 hover:text-black dark:hover:text-white'
						}`}
						use:ripple
					>
						<BookOpen size={14} class={isLibraryActive ? 'text-[#b23a2e] dark:text-[#e08a63]' : ''} />
						<span>Library</span>
					</a>

					<a
						href="/app/glossary/"
						class={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition-all duration-200 ${
							isGlossaryActive
								? 'bg-white font-bold text-black shadow-xs dark:bg-[#221e1a] dark:text-white'
								: 'font-medium opacity-65 hover:opacity-100 hover:text-black dark:hover:text-white'
						}`}
						use:ripple
					>
						<Languages size={14} class={isGlossaryActive ? 'text-[#b23a2e] dark:text-[#e08a63]' : ''} />
						<span>Glossary</span>
					</a>
				</div>

				<!-- LIVE BACKGROUND TRANSLATION ACTIVITY BADGE -->
				{#each $activeTranslatingChapters as activeJob}
					{@const snap = activeJob.snapshot}
					{@const total = snap?.totalPages || snap?.pages.length || 0}
					{@const done = snap?.completedPages || 0}
					<div
						class="hidden sm:flex items-center gap-1.5 rounded-full border border-[#b23a2e]/30 bg-[#b23a2e]/10 px-2.5 py-1 text-xs font-bold text-[#b23a2e] dark:text-[#e08a63] animate-pulse shadow-xs"
						title="Background Translation in Progress"
					>
						<span class="h-1.5 w-1.5 rounded-full bg-[#b23a2e] dark:bg-[#e08a63]"></span>
						<span>Translating ({done}/{total})</span>
					</div>
				{/each}
			</div>

			<!-- RIGHT: TACTILE THEME TOGGLE & SETTINGS BUTTONS -->
			<div class="flex items-center gap-2">
				<!-- THEME QUICK TOGGLE BUTTON (CYCLES ALL THEMES) -->
				<button
					type="button"
					on:click={cycleTheme}
					class="flex h-9 w-9 items-center justify-center rounded-xl border border-black/10 bg-white/70 text-current shadow-2xs backdrop-blur transition-all duration-200 hover:border-black/25 hover:bg-white hover:shadow-xs active:scale-95 dark:border-white/10 dark:bg-white/[0.04] dark:hover:border-white/20 dark:hover:bg-white/[0.08]"
					aria-label="Cycle theme"
					title={`Current theme: ${THEMES.find((item) => item.id === $settings.theme)?.label || $settings.theme}. Click to cycle themes.`}
					use:ripple
				>
					{#if $settings.theme === 'light'}
						<Sun size={17} class="text-amber-500 transition-transform duration-300 hover:rotate-45" />
					{:else if $settings.theme === 'sepia'}
						<Coffee size={17} class="text-[#8c6b4f] transition-transform duration-300 hover:-rotate-12" />
					{:else if $settings.theme === 'dark'}
						<Moon size={17} class="text-indigo-400 transition-transform duration-300 hover:-rotate-12" />
					{:else if $settings.theme === 'oled'}
						<Sparkles size={17} class="text-neutral-300 transition-transform duration-300 hover:scale-110" />
					{:else}
						<Contrast size={17} class="text-white transition-transform duration-300 hover:rotate-180" />
					{/if}
				</button>

				<!-- SETTINGS DIALOG BUTTON -->
				<button
					type="button"
					on:click={() => (settingsOpen = true)}
					class="group flex h-9 w-9 items-center justify-center rounded-xl border border-black/10 bg-white/70 text-current shadow-2xs backdrop-blur transition-all duration-200 hover:border-black/25 hover:bg-white hover:shadow-xs active:scale-95 dark:border-white/10 dark:bg-white/[0.04] dark:hover:border-white/20 dark:hover:bg-white/[0.08]"
					aria-label="Settings"
					title="Preferences & Model Configuration"
					use:ripple
				>
					<Settings size={17} class="opacity-75 transition-transform duration-300 group-hover:rotate-45 group-hover:opacity-100" />
				</button>
			</div>
		</nav>
	</header>

	<!-- PAGE CONTENT -->
	<main class="mx-auto w-full max-w-6xl px-4 pt-6 pb-16 sm:px-6">
		<slot />
	</main>
</div>

<!-- GLOBAL SETTINGS & PREFERENCES MODAL -->
<Modal open={settingsOpen} title="Preferences & Model Configuration" size="sm" on:close={() => (settingsOpen = false)}>
	<div class="flex flex-col gap-5">
		<!-- TRANSLATION ENGINE MODEL -->
		<div>
			<label class="mb-1.5 block text-xs font-semibold opacity-70">Translation Engine Model</label>
			<div class="grid grid-cols-2 gap-2.5">
				{#each TRANSLATION_MODELS as m}
					<button
						type="button"
						on:click={() => setModel(m.id)}
						class={`relative flex flex-col justify-between rounded-xl border p-3 text-left transition-all duration-200 ${
							$settings.model === m.id
								? 'border-[#b23a2e] bg-[#b23a2e]/10 text-[#b23a2e] dark:text-[#e08a63] ring-2 ring-[#b23a2e]/30'
								: 'border-black/10 hover:border-black/20 hover:bg-black/[0.02] dark:border-white/10 dark:hover:border-white/20 dark:hover:bg-white/[0.02]'
						}`}
						use:ripple
					>
						<div class="flex items-center justify-between">
							<div class="flex items-center gap-1.5 font-bold text-xs">
								{#if m.id === 'deepseek-v4-pro'}
									<Sparkles size={13} class="text-amber-500" />
								{:else}
									<Cpu size={13} class="opacity-60" />
								{/if}
								<span>{m.label}</span>
							</div>
							{#if $settings.model === m.id}
								<Check size={14} class="text-[#b23a2e] dark:text-[#e08a63]" />
							{/if}
						</div>
						<div class="mt-1.5 text-[10px] opacity-60 leading-tight">{m.blurb}</div>
					</button>
				{/each}
			</div>
		</div>

		<!-- APPEARANCE THEMES -->
		<div>
			<label class="mb-1.5 block text-xs font-semibold opacity-70">Appearance Theme</label>
			<div class="grid grid-cols-5 gap-1.5">
				{#each THEMES as t}
					<button
						type="button"
						on:click={() => setTheme(t.id)}
						class={`flex flex-col items-center rounded-xl border p-2 text-center text-xs transition-all duration-200 ${
							$settings.theme === t.id
								? 'border-[#b23a2e] ring-2 ring-[#b23a2e]/40 bg-[#b23a2e]/5'
								: 'border-black/10 hover:border-black/25 dark:border-white/10 dark:hover:border-white/25'
						}`}
						use:ripple
					>
						<span class={`mb-1 h-3.5 w-3.5 rounded-full border ${t.dot}`}></span>
						<span class="text-[10px] font-medium">{t.label}</span>
					</button>
				{/each}
			</div>
		</div>

		<!-- DEFAULT LANGUAGES -->
		<div class="grid grid-cols-2 gap-3 border-t border-black/10 pt-4 dark:border-white/10">
			<div>
				<span class="mb-1 block text-xs font-semibold opacity-60">Default Source</span>
				<LanguagePicker value={$settings.sourceLang} on:change={(e) => updateSourceLang(e.detail)} />
			</div>

			<div>
				<span class="mb-1 block text-xs font-semibold opacity-60">Default Target</span>
				<LanguagePicker value={$settings.targetLang} on:change={(e) => updateTargetLang(e.detail)} />
			</div>
		</div>
	</div>

	<svelte:fragment slot="footer">
		<Button variant="primary" on:click={() => (settingsOpen = false)}>Done</Button>
	</svelte:fragment>
</Modal>
