<script lang="ts">
	import { createEventDispatcher, onMount, onDestroy } from 'svelte';
	import { Badge, ActionMenu, type MenuAction } from '$lib/components/ui';
	import GripVertical from 'lucide-svelte/icons/grip-vertical';
	import Eye from 'lucide-svelte/icons/eye';
	import ArrowUp from 'lucide-svelte/icons/arrow-up';
	import Sparkles from 'lucide-svelte/icons/sparkles';
	import Layers from 'lucide-svelte/icons/layers';
	import RotateCcw from 'lucide-svelte/icons/rotate-ccw';
	import Trash2 from 'lucide-svelte/icons/trash-2';
	import Square from 'lucide-svelte/icons/square';

	export let pages: any[] = [];
	export let running = false;
	export let webtoonKind: 'output' | 'original' = 'output';
	export let webtoonWidth: 'sm' | 'md' | 'lg' = 'md';
	export let reloadKey = Date.now();
	export let draggedPageIndex: number | null = null;
	export let dragOverPageIndex: number | null = null;

	const dispatch = createEventDispatcher<{
		inspect: any;
		menuAction: { action: string; page: any };
		dragStart: { event: DragEvent; index: number };
		dragOver: { event: DragEvent; index: number };
		drop: { event: DragEvent; index: number };
		dragEnd: DragEvent;
		toggleKind: void;
	}>();

	const widthClasses = {
		sm: 'max-w-lg',
		md: 'max-w-2xl',
		lg: 'max-w-4xl',
	};

	const statusVariant: Record<string, any> = {
		pending: 'neutral',
		processing: 'warning',
		done: 'success',
		error: 'danger',
	};

	const statusLabel: Record<string, string> = {
		pending: 'Pending',
		processing: 'Processing',
		done: 'Translated',
		error: 'Error',
	};

	const stepBadgeLabels: Record<string, string> = {
		preprocess: 'Cleaning...',
		analyze: 'Detect & OCR...',
		persist_regions: 'Saving...',
		term_extract: 'Extracting...',
		match_glossary: 'Glossary...',
		translate: 'Translating...',
		persist_translations: 'Saving...',
		clean: 'Inpainting...',
		typeset: 'Typesetting...',
		save_output: 'Saving...',
	};

	let currentScrollPage = 1;

	function handleScroll() {
		const elements = document.querySelectorAll('[data-page-seq]');
		const scrollPos = window.scrollY + window.innerHeight / 3;
		for (let i = 0; i < elements.length; i++) {
			const el = elements[i] as HTMLElement;
			const top = el.offsetTop;
			const height = el.offsetHeight;
			if (scrollPos >= top && scrollPos < top + height) {
				currentScrollPage = i + 1;
				break;
			}
		}
	}

	onMount(() => {
		window.addEventListener('scroll', handleScroll, { passive: true });
	});

	onDestroy(() => {
		window.removeEventListener('scroll', handleScroll);
	});

	function getMenuItems(pg: any, idx: number, isJobRunning: boolean): MenuAction[] {
		const items: MenuAction[] = [];
		const isPageProcessing = pg.status === 'processing';

		if (isPageProcessing) {
			items.push({
				value: 'cancel',
				label: 'Cancel Translation',
				icon: Square,
				danger: true,
			});
		} else {
			items.push({
				value: 'translate',
				label: pg.status === 'done' ? 'Re-translate Page' : 'Translate Page',
				icon: Sparkles,
				disabled: isPageProcessing,
			});
		}

		items.push({
			value: 'inspect',
			label: 'Inspect Page',
			icon: Eye,
		});

		if (idx < pages.length - 1) {
			items.push({
				value: 'stitch',
				label: `Merge with Page ${pg.seq + 2}`,
				icon: Layers,
				disabled: isJobRunning || isPageProcessing,
			});
		}

		items.push({
			value: 'reset',
			label: 'Clear Progress',
			icon: RotateCcw,
			disabled: isJobRunning || isPageProcessing || (pg.status === 'pending' && !pg.outputPath),
		});

		items.push({
			value: 'delete',
			label: 'Delete Page',
			icon: Trash2,
			danger: true,
			disabled: isJobRunning || isPageProcessing,
		});

		return items;
	}
</script>

<div class="flex flex-col items-center gap-6 w-full">
	<div class={`w-full ${widthClasses[webtoonWidth]} flex flex-col items-center bg-black transition-all duration-300 shadow-2xl`}>
		{#each pages as page, idx (page.id)}
			<div
				draggable="true"
				on:dragstart={(e) => dispatch('dragStart', { event: e, index: idx })}
				on:dragover={(e) => dispatch('dragOver', { event: e, index: idx })}
				on:drop={(e) => dispatch('drop', { event: e, index: idx })}
				on:dragend={(e) => dispatch('dragEnd', e)}
				class={`group relative w-full border-0 p-0 m-0 leading-none bg-black transition-all ${
					dragOverPageIndex === idx ? 'ring-4 ring-[#b23a2e] z-10 scale-[1.01]' : ''
				} ${draggedPageIndex === idx ? 'opacity-40' : ''}`}
				data-page-seq={page.seq}
			>
				<img
					src={`/api/pages/${page.id}/file?kind=${webtoonKind === 'output' && page.outputPath ? 'output' : 'original'}&v=${page.outputPath || 'orig'}_${reloadKey}`}
					alt={`Page ${page.seq + 1}`}
					draggable="false"
					class="w-full block h-auto object-contain leading-none border-0 p-0 m-0 select-none pointer-events-none"
					loading="lazy"
					decoding="async"
				/>

				<!-- HOVER-ONLY OVERLAYS -->
				<div class="absolute bottom-3 left-3 flex items-center gap-2 opacity-0 transition-opacity duration-200 group-hover:opacity-100">
					<span class="flex items-center gap-1 cursor-grab rounded-md bg-black/80 px-2 py-0.5 text-[11px] font-bold text-white backdrop-blur border border-white/10 active:cursor-grabbing">
						<GripVertical size={12} class="opacity-60" /> Page {page.seq + 1}
					</span>
					<Badge
						variant={statusVariant[page.status]}
						class={page.status === 'done'
							? 'text-emerald-300 bg-emerald-950/80 border border-emerald-500/40 backdrop-blur shadow-md'
							: page.status === 'processing'
								? 'text-amber-300 bg-amber-950/80 border border-amber-500/40 backdrop-blur shadow-md animate-pulse'
								: page.status === 'error'
									? 'text-red-300 bg-red-950/80 border border-red-500/40 backdrop-blur shadow-md'
									: 'text-neutral-200 bg-neutral-900/80 border border-white/20 backdrop-blur shadow-md'}
					>
						{#if page.status === 'processing'}
							{page.currentStep ? (stepBadgeLabels[page.currentStep] || page.currentStep) : 'Processing...'}
						{:else}
							{statusLabel[page.status]}
						{/if}
					</Badge>
				</div>

				<div class="absolute bottom-3 right-3 flex items-center gap-1.5 opacity-0 transition-opacity duration-200 group-hover:opacity-100">
					<button
						type="button"
						on:click={() => dispatch('inspect', page)}
						class="flex items-center gap-1 rounded-md bg-black/80 px-2.5 py-1 text-[11px] font-semibold text-white backdrop-blur transition hover:bg-black pointer-events-auto"
					>
						<Eye size={12} /> Inspect
					</button>
					<ActionMenu
						items={getMenuItems(page, idx, running)}
						on:select={(e) => dispatch('menuAction', { action: e.detail, page })}
						class="bg-black/80 text-white border border-white/20 hover:bg-black pointer-events-auto opacity-100"
					/>
				</div>
			</div>
		{/each}
	</div>
</div>

<!-- FLOATING UNINTRUSIVE WEBTOON DOCK -->
<div class="fixed bottom-6 right-6 z-40 flex items-center gap-2 rounded-full border border-white/15 bg-black/85 px-4 py-2 text-xs font-semibold text-white shadow-2xl backdrop-blur transition-all duration-300 hover:bg-black">
	<span class="text-[11px] font-mono opacity-80">Page {currentScrollPage} / {pages.length}</span>
	{#if pages[currentScrollPage - 1]}
		{@const curPg = pages[currentScrollPage - 1]}
		<Badge
			variant={statusVariant[curPg.status]}
			class={curPg.status === 'done'
				? 'text-emerald-300 bg-emerald-950/80 border border-emerald-500/40'
				: curPg.status === 'processing'
					? 'text-amber-300 bg-amber-950/80 border border-amber-500/40'
					: curPg.status === 'error'
						? 'text-red-300 bg-red-950/80 border border-red-500/40'
						: 'text-neutral-200 bg-neutral-900/80 border border-white/20'}
		>
			{statusLabel[curPg.status]}
		</Badge>
	{/if}
	<span class="h-3 w-px bg-white/20"></span>
	<button
		type="button"
		on:click={() => dispatch('toggleKind')}
		class="rounded-full bg-white/15 px-2.5 py-0.5 text-[10px] font-bold text-white transition hover:bg-white/30"
	>
		{webtoonKind === 'output' ? 'Translation' : 'Original'}
	</button>
	<span class="h-3 w-px bg-white/20"></span>
	<button
		type="button"
		on:click={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
		class="flex items-center justify-center rounded-full p-1 transition hover:bg-white/20"
		title="Scroll to top"
	>
		<ArrowUp size={14} />
	</button>
</div>
