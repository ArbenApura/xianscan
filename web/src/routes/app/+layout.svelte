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
	import { SOURCE_LANGUAGE_OPTIONS, TARGET_LANGUAGE_OPTIONS } from '$lib/languages';
	// IMPORTED ICONS
	import BookOpen from 'lucide-svelte/icons/book-open';
	import Languages from 'lucide-svelte/icons/languages';
	import Palette from 'lucide-svelte/icons/palette';
	import Cpu from 'lucide-svelte/icons/cpu';
	import Settings from 'lucide-svelte/icons/settings';
	import Check from 'lucide-svelte/icons/check';

	// IMPORTED UI COMPONENTS
	import Modal from '$lib/components/ui/Modal.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Select from '$lib/components/ui/Select.svelte';
	import LanguagePicker from '$lib/components/ui/LanguagePicker.svelte';

	// -- STATES -- //
	let settingsOpen = false;
	let themeMenuOpen = false;
	let modelMenuOpen = false;

	const THEMES: { id: Theme; label: string; bg: string; dot: string }[] = [
		{ id: 'light', label: 'Light', bg: 'bg-[#fbfaf7]', dot: 'border-slate-300 bg-[#fbfaf7]' },
		{ id: 'sepia', label: 'Sepia', bg: 'bg-[#f4ecd8]', dot: 'border-[#d4c3a3] bg-[#f4ecd8]' },
		{ id: 'dark', label: 'Dark', bg: 'bg-[#13100c]', dot: 'border-neutral-700 bg-[#13100c]' },
		{ id: 'oled', label: 'OLED', bg: 'bg-black', dot: 'border-neutral-800 bg-black' },
		{ id: 'contrast', label: 'Contrast', bg: 'bg-black', dot: 'border-white bg-black' },
	];

	function setTheme(t: Theme) {
		settings.update((s) => ({ ...s, theme: t }));
		themeMenuOpen = false;
		toast.success(`Theme updated to ${t}`);
	}

	function setModel(m: string) {
		settings.update((s) => ({ ...s, model: m }));
		modelMenuOpen = false;
		toast.success(`Translation model set to ${m === 'deepseek-v4-pro' ? 'Pro' : 'Flash'}`);
	}

	function updateSourceLang(lang: string) {
		settings.update((s) => ({ ...s, sourceLang: lang }));
	}

	function updateTargetLang(lang: string) {
		settings.update((s) => ({ ...s, targetLang: lang }));
	}

	$: activePath = $page.url.pathname;
</script>

<!-- APP SHELL — THEMED SURFACE + TOP NAV -->
<div class={THEME_CLASS[$settings.theme] + ' min-h-screen font-sans transition-colors duration-200'}>
	<!-- TOP BAR WITH DYNAMIC THEME SURFACE -->
	<header
		class={`sticky top-0 z-40 border-b border-black/[0.06] backdrop-blur-md transition-colors duration-200 dark:border-white/[0.06] ${THEME_BAR[$settings.theme]}`}
	>
		<nav class="mx-auto flex w-full max-w-6xl items-center gap-3 px-4 py-2.5 sm:px-6">
			<!-- BRAND / LOGO -->
			<a
				href="/app/"
				class="flex items-center gap-2.5 text-base font-bold tracking-tight text-current transition opacity-90 hover:opacity-100"
			>
				<div class="flex h-7 w-7 items-center justify-center rounded-lg bg-[#b23a2e] text-xs font-black text-white shadow-sm">
					譯
				</div>
				<span class="hidden sm:inline">Manua Translator</span>
			</a>

			<!-- BREADCRUMB NAV PILLS -->
			<div class="ml-2 flex items-center gap-1">
				<a
					href="/app/"
					class={`flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium transition ${
						activePath === '/app/' || activePath.startsWith('/app/books')
							? 'bg-[#b23a2e]/12 text-[#b23a2e] dark:text-[#e08a63]'
							: 'opacity-70 hover:bg-black/5 hover:opacity-100 dark:hover:bg-white/5'
					}`}
					use:ripple
				>
					<BookOpen size={14} />
					<span>Library</span>
				</a>

				<a
					href="/app/glossary"
					class={`flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium transition ${
						activePath.startsWith('/app/glossary')
							? 'bg-[#b23a2e]/12 text-[#b23a2e] dark:text-[#e08a63]'
							: 'opacity-70 hover:bg-black/5 hover:opacity-100 dark:hover:bg-white/5'
					}`}
					use:ripple
				>
					<Languages size={14} />
					<span>Glossary</span>
				</a>
			</div>

			<div class="ml-auto flex items-center gap-2">
				<!-- MODEL SELECTOR BUTTON / DROPDOWN -->
				<div class="relative">
					<button
						type="button"
						class="flex items-center gap-1.5 rounded-lg border border-black/10 px-2.5 py-1 text-xs font-medium transition hover:border-[#b23a2e]/40 dark:border-white/10"
						on:click={() => {
							modelMenuOpen = !modelMenuOpen;
							themeMenuOpen = false;
						}}
						use:ripple
						aria-label="Select Model"
					>
						<Cpu size={13} class="text-[#b23a2e] dark:text-[#e08a63]" />
						<span class="capitalize">{$settings.model.includes('pro') ? 'DeepSeek Pro' : 'DeepSeek Flash'}</span>
					</button>

					{#if modelMenuOpen}
						<!-- BACKDROP DISMISS -->
						<button
							type="button"
							class="fixed inset-0 z-40 bg-transparent"
							on:click={() => (modelMenuOpen = false)}
							aria-hidden="true"
						></button>

						<div class="absolute right-0 top-full z-50 mt-1.5 w-64 rounded-xl border border-black/10 bg-white p-1.5 shadow-xl dark:border-white/10 dark:bg-[#1a1713]">
							<div class="px-2 py-1 text-[11px] font-semibold uppercase tracking-wider opacity-50">
								Translation Model
							</div>
							{#each TRANSLATION_MODELS as m}
								<button
									type="button"
									on:click={() => setModel(m.id)}
									class={`flex w-full items-start justify-between rounded-lg p-2 text-left text-xs transition ${
										$settings.model === m.id
											? 'bg-[#b23a2e]/10 text-[#b23a2e] dark:text-[#e08a63]'
											: 'hover:bg-black/5 dark:hover:bg-white/5'
									}`}
								>
									<div>
										<div class="font-semibold">{m.label}</div>
										<div class="mt-0.5 text-[11px] opacity-60">{m.blurb}</div>
									</div>
									{#if $settings.model === m.id}
										<Check size={14} class="mt-0.5 shrink-0" />
									{/if}
								</button>
							{/each}
						</div>
					{/if}
				</div>

				<!-- THEME SELECTOR BUTTON / POPUP -->
				<div class="relative">
					<button
						type="button"
						class="flex items-center gap-1.5 rounded-lg border border-black/10 px-2.5 py-1 text-xs font-medium transition hover:border-[#b23a2e]/40 dark:border-white/10"
						on:click={() => {
							themeMenuOpen = !themeMenuOpen;
							modelMenuOpen = false;
						}}
						use:ripple
						aria-label="Select Theme"
					>
						<Palette size={13} class="text-[#b23a2e] dark:text-[#e08a63]" />
						<span class="capitalize hidden xs:inline">{$settings.theme}</span>
					</button>

					{#if themeMenuOpen}
						<!-- BACKDROP DISMISS -->
						<button
							type="button"
							class="fixed inset-0 z-40 bg-transparent"
							on:click={() => (themeMenuOpen = false)}
							aria-hidden="true"
						></button>

						<div class="absolute right-0 top-full z-50 mt-1.5 w-44 rounded-xl border border-black/10 bg-white p-1.5 shadow-xl dark:border-white/10 dark:bg-[#1a1713]">
							<div class="px-2 py-1 text-[11px] font-semibold uppercase tracking-wider opacity-50">
								Theme
							</div>
							{#each THEMES as t}
								<button
									type="button"
									on:click={() => setTheme(t.id)}
									class={`flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-xs transition ${
										$settings.theme === t.id
											? 'bg-[#b23a2e]/10 text-[#b23a2e] dark:text-[#e08a63]'
											: 'hover:bg-black/5 dark:hover:bg-white/5'
									}`}
								>
									<div class="flex items-center gap-2">
										<span class={`h-3 w-3 rounded-full border ${t.dot}`}></span>
										<span class="font-medium">{t.label}</span>
									</div>
									{#if $settings.theme === t.id}
										<Check size={14} />
									{/if}
								</button>
							{/each}
						</div>
					{/if}
				</div>

				<!-- SETTINGS BUTTON -->
				<button
					type="button"
					on:click={() => (settingsOpen = true)}
					class="rounded-lg border border-black/10 p-1.5 text-current opacity-70 transition hover:opacity-100 dark:border-white/10"
					aria-label="Settings"
					use:ripple
				>
					<Settings size={15} />
				</button>
			</div>
		</nav>
	</header>

	<!-- PAGE CONTENT -->
	<main class="mx-auto w-full max-w-6xl px-4 pb-16 sm:px-6">
		<slot />
	</main>
</div>

<!-- GLOBAL SETTINGS MODAL -->
<Modal open={settingsOpen} title="Preferences & Defaults" size="sm" on:close={() => (settingsOpen = false)}>
	<div class="flex flex-col gap-4">
		<div>
			<span class="mb-1 block text-xs font-semibold opacity-60">Default Source Language</span>
			<LanguagePicker value={$settings.sourceLang} on:change={(e) => updateSourceLang(e.detail)} />
		</div>

		<div>
			<span class="mb-1 block text-xs font-semibold opacity-60">Default Target Language</span>
			<LanguagePicker value={$settings.targetLang} on:change={(e) => updateTargetLang(e.detail)} />
		</div>

		<div>
			<label class="mb-1 block text-xs font-semibold opacity-60">Translation Engine Model</label>
			<div class="grid grid-cols-2 gap-2">
				{#each TRANSLATION_MODELS as m}
					<button
						type="button"
						on:click={() => setModel(m.id)}
						class={`rounded-lg border p-2.5 text-left text-xs transition ${
							$settings.model === m.id
								? 'border-[#b23a2e] bg-[#b23a2e]/10 text-[#b23a2e] dark:text-[#e08a63]'
								: 'border-black/10 hover:bg-black/5 dark:border-white/10 dark:hover:bg-white/5'
						}`}
					>
						<div class="font-bold">{m.label}</div>
						<div class="mt-0.5 text-[10px] opacity-60">{m.blurb}</div>
					</button>
				{/each}
			</div>
		</div>

		<div>
			<label class="mb-1 block text-xs font-semibold opacity-60">Appearance Theme</label>
			<div class="grid grid-cols-5 gap-1.5">
				{#each THEMES as t}
					<button
						type="button"
						on:click={() => setTheme(t.id)}
						class={`flex flex-col items-center rounded-lg border p-2 text-center text-xs transition ${
							$settings.theme === t.id
								? 'border-[#b23a2e] ring-2 ring-[#b23a2e]/30'
								: 'border-black/10 hover:border-black/30 dark:border-white/10'
						}`}
					>
						<span class={`mb-1.5 h-4 w-4 rounded-full border ${t.dot}`}></span>
						<span class="text-[10px] font-medium">{t.label}</span>
					</button>
				{/each}
			</div>
		</div>
	</div>

	<svelte:fragment slot="footer">
		<Button on:click={() => (settingsOpen = false)}>Done</Button>
	</svelte:fragment>
</Modal>
