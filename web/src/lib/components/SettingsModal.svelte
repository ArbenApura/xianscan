<script lang="ts">
	// IMPORTED DEP-MODULES
	import { toast } from 'svelte-sonner';
	// IMPORTED MODULES
	import { ripple } from '$lib/actions/ripple';
	import {
		settings,
		TRANSLATION_MODELS,
		INPAINT_MODES,
		EXECUTION_DEVICES,
		APP_FONTS,
		type Theme,
		type AppFont,
		type InpaintMode,
		type ExecutionDevice,
	} from '$lib/stores/settings';
	// IMPORTED ICONS
	import Languages from 'lucide-svelte/icons/languages';
	import Check from 'lucide-svelte/icons/check';
	import Cpu from 'lucide-svelte/icons/cpu';
	import Sparkles from 'lucide-svelte/icons/sparkles';
	import Zap from 'lucide-svelte/icons/zap';
	import Layers from 'lucide-svelte/icons/layers';
	import Maximize2 from 'lucide-svelte/icons/maximize-2';
	import Activity from 'lucide-svelte/icons/activity';
	import Type from 'lucide-svelte/icons/type';
	import Scissors from 'lucide-svelte/icons/scissors';

	// IMPORTED UI COMPONENTS
	import Modal from '$lib/components/ui/Modal.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import LanguagePicker from '$lib/components/ui/LanguagePicker.svelte';

	// -- PROPS & EVENTS -- //
	export let open = false;

	// -- STATES -- //
	let activeSettingsTab: 'ai' | 'compute' | 'general' = 'ai';

	interface HardwareInfo {
		device_label: string;
		active_provider: string;
		providers: string[];
		available_providers: string[];
		has_cuda: boolean;
		has_directml: boolean;
		has_coreml: boolean;
	}

	let hardwareInfo: HardwareInfo | null = null;
	let hardwareLoading = false;

	async function loadHardwareStatus() {
		hardwareLoading = true;
		try {
			const res = await fetch('/api/system/hardware');
			if (res.ok) {
				hardwareInfo = (await res.json()) as HardwareInfo;
			}
		} catch {
			// Silent fallback
		} finally {
			hardwareLoading = false;
		}
	}

	$: if (open) {
		loadHardwareStatus();
	}

	const THEMES: { id: Theme; label: string; dot: string }[] = [
		{ id: 'light', label: 'Light', dot: 'border-slate-300 bg-[#fbfaf7]' },
		{ id: 'sepia', label: 'Sepia', dot: 'border-[#d4c3a3] bg-[#f4ecd8]' },
		{ id: 'dark', label: 'Dark', dot: 'border-neutral-700 bg-[#13100c]' },
	];

	function formatDeviceLabel(label?: string): string {
		if (!label) return 'Detecting...';
		return label
			.replace(/\s*\(Forced via MT_DEVICE=[^)]+\)/i, '')
			.replace(/\s*\(Standard\)/i, '')
			.replace(/\s*\/ AMD & Intel & NVIDIA/i, '')
			.trim();
	}

	function setTheme(t: Theme | string) {
		settings.update((s) => ({ ...s, theme: t as Theme }));
		const label = THEMES.find((item) => item.id === t)?.label || t;
		toast.success(`Theme updated to ${label}`);
	}

	function setAppFont(f: AppFont) {
		settings.update((s) => ({ ...s, appFont: f }));
		const found = APP_FONTS.find((item) => item.id === f);
		toast.success(`System font updated to ${found?.label || f}`);
	}

	function setModel(m: string) {
		settings.update((s) => ({ ...s, model: m }));
		toast.success(`Model set to ${m === 'deepseek-v4-pro' ? 'DeepSeek Pro' : 'DeepSeek Flash'}`);
	}

	function setInpaintMode(mode: InpaintMode) {
		settings.update((s) => ({ ...s, inpaintMode: mode }));
		const found = INPAINT_MODES.find((i) => i.id === mode);
		toast.success(`Inpainting strategy set to ${found?.label || mode}`);
	}

	function setParallelProcesses(n: number) {
		settings.update((s) => ({ ...s, parallelProcesses: n }));
		toast.success(`Parallel page workers set to ${n}`);
	}

	function setParallelChapters(n: number) {
		settings.update((s) => ({ ...s, parallelChapters: n }));
		toast.success(`Parallel batch chapters set to ${n}`);
	}

	function toggleResliceBeforeBatch() {
		settings.update((s) => {
			const next = !s.resliceBeforeBatch;
			toast.success(`Pre-translation smart reslicing ${next ? 'enabled' : 'disabled'}`);
			return { ...s, resliceBeforeBatch: next };
		});
	}

	function isDeviceAvailable(devId: ExecutionDevice): boolean {
		if (!hardwareInfo) return true;
		if (devId === 'auto' || devId === 'cpu') return true;
		if (devId === 'cuda') return hardwareInfo.has_cuda;
		if (devId === 'dml') return hardwareInfo.has_directml;
		return true;
	}

	function getDeviceAvailabilityReason(devId: ExecutionDevice): string | null {
		if (!hardwareInfo) return null;
		if (devId === 'cuda' && !hardwareInfo.has_cuda) return 'CUDA / GPU provider not detected';
		if (devId === 'dml' && !hardwareInfo.has_directml) return 'DirectML DirectX 12 provider not detected';
		return null;
	}

	async function setExecutionDevice(dev: ExecutionDevice) {
		if (!isDeviceAvailable(dev)) {
			const reason = getDeviceAvailabilityReason(dev);
			toast.error(`Cannot select ${dev.toUpperCase()}: ${reason || 'Hardware not supported'}`);
			return;
		}

		settings.update((s) => ({ ...s, executionDevice: dev }));
		const found = EXECUTION_DEVICES.find((d) => d.id === dev);
		toast.success(`Compute hardware set to ${found?.label || dev}`);

		try {
			const res = await fetch('/api/system/hardware', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ device: dev }),
			});
			if (res.ok) {
				hardwareInfo = (await res.json()) as HardwareInfo;
			}
		} catch {
			// Ignore offline
		}
	}

	function updateSourceLang(lang: string) {
		settings.update((s) => ({ ...s, sourceLang: lang }));
	}

	function updateTargetLang(lang: string) {
		settings.update((s) => ({ ...s, targetLang: lang }));
	}
</script>

<!-- GLOBAL SETTINGS & PREFERENCES MODAL -->
<Modal {open} title="Preferences & Configuration" size="lg" on:close={() => (open = false)}>
	<div class="flex flex-col gap-4 sm:gap-5">
		<!-- ELEGANT SEGMENTED TABS (RESPONSIVE FOR ALL SCREEN WIDTHS) -->
		<div class="grid grid-cols-3 gap-1 rounded-xl border border-black/[0.08] bg-black/[0.03] p-1 dark:border-white/[0.08] dark:bg-white/[0.04]">
			<button
				type="button"
				on:click={() => (activeSettingsTab = 'ai')}
				class={`flex items-center justify-center gap-1.5 rounded-lg px-1 py-1.5 sm:px-3 sm:py-2 text-[11px] sm:text-xs font-bold transition-all duration-150 min-w-0 ${
					activeSettingsTab === 'ai'
						? 'bg-white text-[#b23a2e] shadow-xs dark:bg-[#25201b] dark:text-[#e08a63]'
						: 'opacity-65 hover:opacity-100 hover:bg-black/[0.02] dark:hover:bg-white/[0.02]'
				}`}
				use:ripple
			>
				<Sparkles size={13} class={`shrink-0 ${activeSettingsTab === 'ai' ? 'text-[#b23a2e] dark:text-[#e08a63]' : ''}`} />
				<span class="truncate px-0.5">
					AI<span class="hidden sm:inline"> & Models</span>
				</span>
			</button>

			<button
				type="button"
				on:click={() => (activeSettingsTab = 'compute')}
				class={`flex items-center justify-center gap-1.5 rounded-lg px-1 py-1.5 sm:px-3 sm:py-2 text-[11px] sm:text-xs font-bold transition-all duration-150 min-w-0 ${
					activeSettingsTab === 'compute'
						? 'bg-white text-[#b23a2e] shadow-xs dark:bg-[#25201b] dark:text-[#e08a63]'
						: 'opacity-65 hover:opacity-100 hover:bg-black/[0.02] dark:hover:bg-white/[0.02]'
				}`}
				use:ripple
			>
				<Cpu size={13} class={`shrink-0 ${activeSettingsTab === 'compute' ? 'text-[#b23a2e] dark:text-[#e08a63]' : ''}`} />
				<span class="truncate px-0.5">
					Compute<span class="hidden sm:inline"> & Speed</span>
				</span>
			</button>

			<button
				type="button"
				on:click={() => (activeSettingsTab = 'general')}
				class={`flex items-center justify-center gap-1.5 rounded-lg px-1 py-1.5 sm:px-3 sm:py-2 text-[11px] sm:text-xs font-bold transition-all duration-150 min-w-0 ${
					activeSettingsTab === 'general'
						? 'bg-white text-[#b23a2e] shadow-xs dark:bg-[#25201b] dark:text-[#e08a63]'
						: 'opacity-65 hover:opacity-100 hover:bg-black/[0.02] dark:hover:bg-white/[0.02]'
				}`}
				use:ripple
			>
				<Languages size={13} class={`shrink-0 ${activeSettingsTab === 'general' ? 'text-[#b23a2e] dark:text-[#e08a63]' : ''}`} />
				<span class="truncate px-0.5">
					General<span class="hidden sm:inline"> & Lang</span>
				</span>
			</button>
		</div>

		<!-- TAB 1: AI & MODELS -->
		{#if activeSettingsTab === 'ai'}
			<div class="flex flex-col gap-5 sm:gap-6 py-1">
				<!-- INPAINTING STRATEGY -->
				<div>
					<div class="mb-2.5 sm:mb-3">
						<div class="text-xs font-bold uppercase tracking-wider opacity-80">Inpainting Strategy</div>
						<p class="text-[11px] opacity-60">Choose how comic text bubbles and watermarks are erased and reconstructed</p>
					</div>

					<div class="grid grid-cols-1 gap-2.5 sm:grid-cols-3 sm:gap-3">
						{#each INPAINT_MODES as mode}
							<button
								type="button"
								on:click={() => setInpaintMode(mode.id)}
								class={`relative flex flex-col justify-between rounded-xl border p-3 sm:p-3.5 text-left transition-all duration-200 ${
									$settings.inpaintMode === mode.id
										? 'border-[#b23a2e] bg-[#b23a2e]/[0.08] text-[#b23a2e] dark:text-[#e08a63] ring-2 ring-[#b23a2e]/30 shadow-xs'
										: 'border-black/10 hover:border-black/20 hover:bg-black/[0.02] dark:border-white/10 dark:hover:border-white/20 dark:hover:bg-white/[0.02]'
								}`}
								use:ripple
							>
								<div>
									<div class="flex items-center justify-between">
										<div class="flex items-center gap-1.5 font-bold text-xs">
											{#if mode.id === 'patch'}
												<Zap size={14} class="text-emerald-500 shrink-0" />
											{:else if mode.id === 'scaled'}
												<Layers size={14} class="text-amber-500 shrink-0" />
											{:else}
												<Maximize2 size={14} class="text-sky-500 shrink-0" />
											{/if}
											<span>{mode.label}</span>
										</div>
										{#if $settings.inpaintMode === mode.id}
											<Check size={14} class="text-[#b23a2e] dark:text-[#e08a63] shrink-0" />
										{/if}
									</div>
									<div class="mt-1.5 inline-flex items-center rounded-full border px-2 py-0.5 text-[9px] font-bold tracking-wide {mode.badgeColor}">
										{mode.tag}
									</div>
								</div>
								<div class="mt-2.5 text-[11px] opacity-75 leading-relaxed">{mode.blurb}</div>
							</button>
						{/each}
					</div>
				</div>

				<!-- TRANSLATION ENGINE MODEL -->
				<div class="border-t border-black/10 pt-4 dark:border-white/10">
					<div class="mb-2.5 sm:mb-3">
						<div class="text-xs font-bold uppercase tracking-wider opacity-80">Translation Model (LLM)</div>
						<p class="text-[11px] opacity-60">Select DeepSeek language model for Chinese-to-target dialogue rendering</p>
					</div>

					<div class="grid grid-cols-1 gap-2.5 sm:grid-cols-2 sm:gap-3">
						{#each TRANSLATION_MODELS as m}
							<button
								type="button"
								on:click={() => setModel(m.id)}
								class={`relative flex flex-col justify-between rounded-xl border p-3 sm:p-3.5 text-left transition-all duration-200 ${
									$settings.model === m.id
										? 'border-[#b23a2e] bg-[#b23a2e]/[0.08] text-[#b23a2e] dark:text-[#e08a63] ring-2 ring-[#b23a2e]/30 shadow-xs'
										: 'border-black/10 hover:border-black/20 hover:bg-black/[0.02] dark:border-white/10 dark:hover:border-white/20 dark:hover:bg-white/[0.02]'
								}`}
								use:ripple
							>
								<div>
									<div class="flex items-center justify-between">
										<div class="flex items-center gap-1.5 font-bold text-xs">
											{#if m.id === 'deepseek-v4-pro'}
												<Sparkles size={14} class="text-amber-500 shrink-0" />
											{:else}
												<Zap size={14} class="opacity-60 shrink-0" />
											{/if}
											<span>{m.label}</span>
										</div>
										{#if $settings.model === m.id}
											<Check size={14} class="text-[#b23a2e] dark:text-[#e08a63] shrink-0" />
										{/if}
									</div>
									<div class="mt-1 text-[9px] font-semibold opacity-60 uppercase tracking-wider">
										{m.id === 'deepseek-v4-flash' ? 'High Speed · Low Cost' : 'Maximum Literary Accuracy'}
									</div>
								</div>
								<div class="mt-2 text-[11px] opacity-75 leading-relaxed">{m.blurb}</div>
							</button>
						{/each}
					</div>
				</div>
			</div>

		<!-- TAB 2: COMPUTE & PERFORMANCE -->
		{:else if activeSettingsTab === 'compute'}
			<div class="flex flex-col gap-5 sm:gap-6 py-1">
				<!-- HARDWARE COMPUTE ACCELERATOR -->
				<div>
					<div class="mb-2.5 sm:mb-3 flex flex-col gap-1.5 sm:flex-row sm:items-center sm:justify-between">
						<div>
							<div class="text-xs font-bold uppercase tracking-wider opacity-80">Hardware Compute Accelerator</div>
							<p class="text-[11px] opacity-60">Select execution engine for ONNX Runtime models</p>
						</div>
						<!-- LIVE STATUS PILL (MOBILE ADAPTIVE) -->
						{#if hardwareInfo}
							<div
								class="self-start sm:self-auto flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 sm:py-1 text-[10px] font-bold text-emerald-700 dark:text-emerald-300 max-w-full"
								title="Active ONNX Runtime Provider"
							>
								<Activity size={11} class="text-emerald-500 shrink-0 animate-pulse" />
								<span class="truncate px-0.5">{formatDeviceLabel(hardwareInfo.device_label)}</span>
							</div>
						{/if}
					</div>

					<div class="grid grid-cols-1 gap-2 sm:grid-cols-2 sm:gap-2.5">
						{#each EXECUTION_DEVICES as dev}
							{@const available = isDeviceAvailable(dev.id)}
							{@const reason = getDeviceAvailabilityReason(dev.id)}
							<button
								type="button"
								on:click={() => setExecutionDevice(dev.id)}
								class={`relative flex flex-col justify-between rounded-xl border p-3 text-left transition-all duration-200 ${
									!available
										? 'opacity-45 hover:opacity-60 border-black/5 bg-black/[0.01] dark:border-white/5 dark:bg-white/[0.01] cursor-not-allowed'
										: $settings.executionDevice === dev.id
											? 'border-[#b23a2e] bg-[#b23a2e]/[0.08] text-[#b23a2e] dark:text-[#e08a63] ring-2 ring-[#b23a2e]/30 shadow-xs'
											: 'border-black/10 hover:border-black/20 hover:bg-black/[0.02] dark:border-white/10 dark:hover:border-white/20 dark:hover:bg-white/[0.02]'
								}`}
								use:ripple
							>
								<div>
									<div class="flex items-center justify-between gap-2">
										<div class="flex items-center gap-1.5 font-bold text-xs">
											<Cpu size={13} class={`shrink-0 ${available ? 'opacity-80' : 'opacity-40'}`} />
											<span>{dev.label}</span>
										</div>
										{#if $settings.executionDevice === dev.id}
											<Check size={14} class="text-[#b23a2e] dark:text-[#e08a63] shrink-0" />
										{/if}
									</div>
									<div class="mt-1 text-[10px] opacity-70 leading-relaxed">{dev.blurb}</div>
								</div>
								{#if !available}
									<div class="mt-2 flex items-center gap-1.5 text-[9px] text-amber-700 dark:text-amber-300 font-medium">
										<span class="rounded bg-amber-500/15 px-1.5 py-0.5 font-bold uppercase tracking-wider">
											Not Detected
										</span>
										{#if reason}
											<span class="opacity-75">{reason}</span>
										{/if}
									</div>
								{/if}
							</button>
						{/each}
					</div>
				</div>

				<!-- PARALLEL PROCESSES & CONCURRENCY -->
				<div class="border-t border-black/10 pt-4 dark:border-white/10">
					<div class="mb-2.5 sm:mb-3">
						<div class="text-xs font-bold uppercase tracking-wider opacity-80">Parallel Processing & Concurrency</div>
						<p class="text-[11px] opacity-60">Control simultaneous workers for batch and chapter pipelines</p>
					</div>

					<div class="flex flex-col gap-3.5 sm:gap-4">
						<!-- PARALLEL PAGE WORKERS PER CHAPTER -->
						<div class="rounded-xl border border-black/[0.06] bg-black/[0.02] p-3 sm:p-3.5 dark:border-white/[0.06] dark:bg-white/[0.02]">
							<div class="mb-2 flex items-center justify-between text-xs">
								<span class="font-bold opacity-85">Parallel Page Workers</span>
								<span class="font-bold text-[#b23a2e] dark:text-[#e08a63]">{$settings.parallelProcesses} {$settings.parallelProcesses === 1 ? 'page' : 'pages'}</span>
							</div>
							<div class="grid grid-cols-5 gap-1.5 sm:gap-2">
								{#each [1, 2, 3, 4, 6] as count}
									<button
										type="button"
										on:click={() => setParallelProcesses(count)}
										class={`flex flex-col items-center justify-center rounded-lg border py-1.5 sm:py-2 text-xs font-bold transition-all duration-150 ${
											$settings.parallelProcesses === count
												? 'border-[#b23a2e] bg-[#b23a2e]/10 text-[#b23a2e] dark:text-[#e08a63] ring-1 ring-[#b23a2e]/40 shadow-xs'
												: 'border-black/10 hover:border-black/25 dark:border-white/10 dark:hover:border-white/25'
										}`}
										use:ripple
									>
										<span>{count}</span>
										<span class="text-[8px] sm:text-[9px] font-normal opacity-60">
											{count === 1 ? 'Eco' : count === 3 ? 'Default' : count === 6 ? 'Max' : `${count}x`}
										</span>
									</button>
								{/each}
							</div>
							<p class="mt-2 text-[10px] opacity-60 leading-tight">Controls how many pages in a single chapter undergo detection, OCR, inpainting, and typesetting at once.</p>
						</div>

						<!-- PARALLEL CHAPTERS IN BATCH -->
						<div class="rounded-xl border border-black/[0.06] bg-black/[0.02] p-3 sm:p-3.5 dark:border-white/[0.06] dark:bg-white/[0.02]">
							<div class="mb-2 flex items-center justify-between text-xs">
								<span class="font-bold opacity-85">Parallel Batch Chapters</span>
								<span class="font-bold text-[#b23a2e] dark:text-[#e08a63]">{$settings.parallelChapters} {($settings.parallelChapters === 1 ? 'chapter' : 'chapters')}</span>
							</div>
							<div class="grid grid-cols-4 gap-1.5 sm:gap-2">
								{#each [1, 2, 3, 4] as count}
									<button
										type="button"
										on:click={() => setParallelChapters(count)}
										class={`flex flex-col items-center justify-center rounded-lg border py-1.5 sm:py-2 text-xs font-bold transition-all duration-150 ${
											$settings.parallelChapters === count
												? 'border-[#b23a2e] bg-[#b23a2e]/10 text-[#b23a2e] dark:text-[#e08a63] ring-1 ring-[#b23a2e]/40 shadow-xs'
												: 'border-black/10 hover:border-black/25 dark:border-white/10 dark:hover:border-white/25'
										}`}
										use:ripple
									>
										<span>{count}</span>
										<span class="text-[8px] sm:text-[9px] font-normal opacity-60">
											{count === 1 ? 'Sequential' : `${count} Parallel`}
										</span>
									</button>
								{/each}
							</div>
							<p class="mt-2 text-[10px] opacity-60 leading-tight">Sequential mode (1) translates chapters one by one. Multi-chapter mode (2–4) runs parallel pipelines.</p>
						</div>

						<!-- SMART PRE-RESLICE BEFORE BATCH TRANSLATION -->
						<div class="rounded-xl border border-black/[0.06] bg-black/[0.02] p-3 sm:p-3.5 dark:border-white/[0.06] dark:bg-white/[0.02]">
							<div class="flex items-center justify-between gap-3">
								<div class="min-w-0 flex-1">
									<div class="flex items-center gap-1.5 font-bold text-xs">
										<Scissors size={13} class="text-[#b23a2e] dark:text-[#e08a63] shrink-0" />
										<span>Smart Pre-Reslice on Batch Translation</span>
									</div>
									<p class="mt-1 text-[10px] opacity-65 leading-relaxed">
										Automatically stitches continuous comic webtoon canvases and detects non-text gutters before translation to prevent cut-off speech bubbles.
									</p>
								</div>

								<!-- TOGGLE SWITCH -->
								<button
									type="button"
									role="switch"
									aria-checked={$settings.resliceBeforeBatch}
									on:click={toggleResliceBeforeBatch}
									class={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-hidden ${
										$settings.resliceBeforeBatch ? 'bg-[#b23a2e] dark:bg-[#e08a63]' : 'bg-black/20 dark:bg-white/20'
									}`}
								>
									<span
										class={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow-md ring-0 transition duration-200 ease-in-out dark:bg-neutral-900 ${
											$settings.resliceBeforeBatch ? 'translate-x-5' : 'translate-x-0'
										}`}
									/>
								</button>
							</div>
						</div>
					</div>
				</div>
			</div>

		<!-- TAB 3: APPEARANCE & LANGUAGES -->
		{:else if activeSettingsTab === 'general'}
			<div class="flex flex-col gap-5 sm:gap-6 py-1">
				<!-- APPEARANCE THEMES -->
				<div>
					<div class="mb-2.5 sm:mb-3">
						<div class="text-xs font-bold uppercase tracking-wider opacity-80">Appearance Theme</div>
						<p class="text-[11px] opacity-60">Choose high-contrast color palette optimized for reading comfort</p>
					</div>

					<div class="grid grid-cols-3 gap-2 sm:gap-3">
						{#each THEMES as t}
							<button
								type="button"
								on:click={() => setTheme(t.id)}
								class={`flex flex-col items-center rounded-xl border p-2.5 sm:p-3.5 text-center text-xs transition-all duration-200 ${
									$settings.theme === t.id
										? 'border-[#b23a2e] ring-2 ring-[#b23a2e]/40 bg-[#b23a2e]/5 shadow-xs font-bold'
										: 'border-black/10 hover:border-black/25 dark:border-white/10 dark:hover:border-white/25 font-medium'
								}`}
								use:ripple
							>
								<span class={`mb-1.5 sm:mb-2 h-3.5 w-3.5 sm:h-4 sm:w-4 rounded-full border shadow-2xs ${t.dot}`}></span>
								<span class="text-[11px] sm:text-xs">{t.label}</span>
							</button>
						{/each}
					</div>
				</div>

				<!-- DEFAULT LANGUAGES -->
				<div class="border-t border-black/10 pt-4 dark:border-white/10">
					<div class="mb-2.5 sm:mb-3">
						<div class="text-xs font-bold uppercase tracking-wider opacity-80">Default Languages</div>
						<p class="text-[11px] opacity-60">Default language pair applied when creating new comic projects</p>
					</div>

					<div class="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4">
						<div>
							<span class="mb-1.5 block text-xs font-semibold opacity-70">Default Source Language</span>
							<LanguagePicker mode="source" value={$settings.sourceLang} on:change={(e) => updateSourceLang(e.detail)} />
						</div>

						<div>
							<span class="mb-1.5 block text-xs font-semibold opacity-70">Default Target Language</span>
							<LanguagePicker value={$settings.targetLang} on:change={(e) => updateTargetLang(e.detail)} />
						</div>
					</div>
				</div>

				<!-- APP SYSTEM FONT -->
				<div class="border-t border-black/10 pt-4 dark:border-white/10">
					<div class="mb-2.5 sm:mb-3">
						<div class="text-xs font-bold uppercase tracking-wider opacity-80">App System Font</div>
						<p class="text-[11px] opacity-60">Select typography family for application UI, dialogue text, and reader overlays</p>
					</div>

					<div class="grid grid-cols-1 gap-2 sm:grid-cols-2 sm:gap-2.5">
						{#each APP_FONTS as f}
							<button
								type="button"
								on:click={() => setAppFont(f.id)}
								class={`relative flex flex-col justify-between rounded-xl border p-3 text-left transition-all duration-200 ${
									$settings.appFont === f.id
										? 'border-[#b23a2e] bg-[#b23a2e]/[0.08] text-[#b23a2e] dark:text-[#e08a63] ring-2 ring-[#b23a2e]/30 shadow-xs'
										: 'border-black/10 hover:border-black/20 hover:bg-black/[0.02] dark:border-white/10 dark:hover:border-white/20 dark:hover:bg-white/[0.02]'
								}`}
								use:ripple
							>
								<div>
									<div class="flex items-center justify-between gap-2">
										<div class="flex items-center gap-1.5 font-bold text-xs">
											<Type size={13} class="shrink-0 opacity-70" />
											<span>{f.label}</span>
										</div>
										{#if $settings.appFont === f.id}
											<Check size={14} class="text-[#b23a2e] dark:text-[#e08a63] shrink-0" />
										{/if}
									</div>
									<div class="mt-1.5 text-xs font-bold tracking-wide px-0.5" style={`font-family: ${f.stack};`}>
										{f.sample}
									</div>
								</div>
								<div class="mt-2 text-[10px] opacity-70 leading-relaxed">{f.blurb}</div>
							</button>
						{/each}
					</div>
				</div>
			</div>
		{/if}
	</div>

	<svelte:fragment slot="footer">
		<Button variant="primary" on:click={() => (open = false)} class="w-full sm:w-auto">Done</Button>
	</svelte:fragment>
</Modal>
