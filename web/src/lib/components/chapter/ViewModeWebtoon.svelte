<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { Badge, ActionMenu, type MenuAction } from '$lib/components/ui';
	import GripVertical from 'lucide-svelte/icons/grip-vertical';
	import Eye from 'lucide-svelte/icons/eye';
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
	export let pageVersions: Record<number, number> = {};
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

<div class="flex flex-col items-center w-[calc(100%+2rem)] -mx-4 sm:w-full sm:mx-0">
	<div class={`w-full ${widthClasses[webtoonWidth]} flex flex-col items-center bg-black transition-all duration-300 shadow-2xl`}>
		{#each pages as page, idx (page.id)}
			{@const hasRatio = Boolean(page.width && page.height)}
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
				data-page-id={page.id}
				style={hasRatio ? `aspect-ratio: ${page.width} / ${page.height};` : ''}
			>
				<div
					class="w-full h-full bg-black/40 overflow-hidden"
					style={hasRatio ? `aspect-ratio: ${page.width} / ${page.height};` : ''}
				>
					<img
						src={`/api/pages/${page.id}/file?kind=${webtoonKind === 'output' && page.outputPath ? 'output' : 'original'}&v=${reloadKey}_${page.outputPath || 'orig'}${pageVersions[page.id] ? `_${pageVersions[page.id]}` : ''}`}
						alt={`Page ${page.seq + 1}`}
						width={page.width || undefined}
						height={page.height || undefined}
						draggable="false"
						class="w-full block h-auto object-contain leading-none border-0 p-0 m-0 select-none pointer-events-none"
						loading="lazy"
						decoding="async"
						style={hasRatio ? `aspect-ratio: ${page.width} / ${page.height};` : ''}
					/>
				</div>

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
