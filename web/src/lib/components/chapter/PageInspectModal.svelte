<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { Modal, Button } from '$lib/components/ui';
	import Copy from 'lucide-svelte/icons/copy';

	export let open = false;
	export let page: any | null = null;
	export let reloadKey = Date.now();

	const dispatch = createEventDispatcher<{
		close: void;
	}>();

	let inspectTab: 'output' | 'cleaned' | 'original' | 'bbox' = 'output';
	let hoveredRegionId: number | null = null;

	$: if (page) {
		if (page.outputPath) inspectTab = 'output';
		else if (page.cleanedPath) inspectTab = 'cleaned';
		else inspectTab = 'original';
	}

	function getBox(rawBox: any): { x: number; y: number; w: number; h: number } | null {
		if (!rawBox) return null;
		if (typeof rawBox === 'string') {
			try {
				return JSON.parse(rawBox);
			} catch {
				return null;
			}
		}
		if (typeof rawBox === 'object') return rawBox;
		return null;
	}

	function copyInspectDebugInfo() {
		if (!page) return;
		const debug = {
			pageId: page.id,
			seq: page.seq,
			dimensions: { width: page.width, height: page.height },
			status: page.status,
			error: page.error,
			regionsCount: page.regions?.length ?? 0,
			regions: (page.regions || []).map((r: any) => ({
				id: r.id,
				seq: r.seq,
				confidence: r.conf,
				box: getBox(r.box),
				sourceOcr: r.textSource,
				translation: r.textTarget,
			})),
		};
		navigator.clipboard?.writeText(JSON.stringify(debug, null, 2));
		toast.success('Page debug JSON copied to clipboard.');
	}
</script>

<Modal
	{open}
	title={`Inspect Page ${page ? page.seq + 1 : ''} (ID: ${page?.id ?? ''})`}
	size="xl"
	on:close={() => dispatch('close')}
>
	{#if page}
		{@const pw = page.width}
		{@const ph = page.height}
		<div class="grid grid-cols-1 gap-6 lg:grid-cols-12">
			<!-- IMAGE / OVERLAY COLUMN -->
			<div class="flex flex-col gap-3 lg:col-span-7">
				<!-- TAB STRIP -->
				<div class="flex flex-wrap items-center gap-1.5 text-xs">
					{#if page.outputPath}
						<button
							type="button"
							class={`rounded-lg px-3 py-1.5 font-medium transition ${
								inspectTab === 'output'
									? 'bg-[#b23a2e] text-white'
									: 'bg-black/5 dark:bg-white/5 hover:bg-black/10 dark:hover:bg-white/10'
							}`}
							on:click={() => (inspectTab = 'output')}
						>
							Typeset Output
						</button>
					{/if}

					{#if page.cleanedPath}
						<button
							type="button"
							class={`rounded-lg px-3 py-1.5 font-medium transition ${
								inspectTab === 'cleaned'
									? 'bg-[#b23a2e] text-white'
									: 'bg-black/5 dark:bg-white/5 hover:bg-black/10 dark:hover:bg-white/10'
							}`}
							on:click={() => (inspectTab = 'cleaned')}
						>
							LaMa Cleaned
						</button>
					{/if}

					<button
						type="button"
						class={`rounded-lg px-3 py-1.5 font-medium transition ${
							inspectTab === 'original'
								? 'bg-[#b23a2e] text-white'
								: 'bg-black/5 dark:bg-white/5 hover:bg-black/10 dark:hover:bg-white/10'
						}`}
						on:click={() => (inspectTab = 'original')}
					>
						Original Image
					</button>

					<!-- REGION MAP -->
					<button
						type="button"
						class={`rounded-lg px-3 py-1.5 font-medium transition ${
							inspectTab === 'bbox'
								? 'bg-emerald-600 text-white'
								: 'bg-black/5 dark:bg-white/5 hover:bg-black/10 dark:hover:bg-white/10'
						}`}
						on:click={() => (inspectTab = 'bbox')}
					>
						🎯 Region Map
					</button>
				</div>

				<!-- PANEL: bbox overlay -->
				{#if inspectTab === 'bbox'}
					<div class="relative overflow-hidden rounded-xl border border-black/10 bg-black/5 dark:border-white/10">
						<img
							src={`/api/pages/${page.id}/file?kind=original&v=${reloadKey}`}
							alt={`Page ${page.seq + 1} original`}
							class="block h-auto w-full object-contain"
							style="max-height: 60vh;"
							loading="lazy"
							decoding="async"
						/>
						{#if pw && ph}
							<svg
								class="pointer-events-none absolute inset-0 h-full w-full"
								viewBox="0 0 {pw} {ph}"
								preserveAspectRatio="xMidYMid meet"
								xmlns="http://www.w3.org/2000/svg"
							>
								{#each page.regions || [] as region (region.id)}
									{@const b = getBox(region.box)}
									{@const bx = b?.x ?? 0}
									{@const by = b?.y ?? 0}
									{@const bw = b?.w ?? 0}
									{@const bh = b?.h ?? 0}
									{@const stroke = '#b23a2e'}
									{@const active = hoveredRegionId === region.id}
									<rect
										x={bx}
										y={by}
										width={bw}
										height={bh}
										fill={active ? `${stroke}26` : 'none'}
										stroke={stroke}
										stroke-width={active ? 6 : 3}
										rx="6"
										opacity={active ? 1 : 0.7}
									/>
									<text
										x={bx + 6}
										y={by + 20}
										font-size="18"
										font-weight="bold"
										fill={stroke}
										stroke="#000"
										stroke-width="4"
										paint-order="stroke"
									>#{region.seq + 1}</text>
								{/each}
							</svg>
						{:else}
							<div class="absolute inset-x-0 bottom-3 flex justify-center">
								<span class="rounded-lg bg-black/70 px-3 py-1.5 text-[11px] font-medium text-white backdrop-blur">
									Run the pipeline first to see bounding boxes
								</span>
							</div>
						{/if}
					</div>
				{:else}
					<div class="overflow-hidden rounded-xl border border-black/10 bg-black/5 dark:border-white/10">
						<img
							src={`/api/pages/${page.id}/file?kind=${inspectTab}&v=${reloadKey}`}
							alt={`Page ${page.seq + 1}`}
							class="block h-auto w-full object-contain"
							style="max-height: 60vh;"
							loading="lazy"
							decoding="async"
						/>
					</div>
				{/if}

				{#if pw && ph}
					<p class="text-[10px] opacity-40 font-mono">{pw} × {ph} px · {page.regions?.length ?? 0} regions</p>
				{/if}
			</div>

			<!-- REGIONS LIST COLUMN -->
			<div class="flex flex-col gap-3 lg:col-span-5 h-full">
				<div class="flex items-center justify-between gap-2">
					<h3 class="text-sm font-bold">
						Detected Regions ({page.regions?.length ?? 0})
					</h3>
				</div>

				{#if !page.regions || page.regions.length === 0}
					<p class="text-xs opacity-60">No text regions detected on this page yet.</p>
				{:else}
					<div class="flex-1 min-h-[300px] max-h-[65vh] space-y-2 overflow-y-auto pr-1">
						{#each page.regions as region (region.id)}
							{@const b = getBox(region.box)}
							<!-- svelte-ignore a11y-no-static-element-interactions -->
							<div
								class={`rounded-lg border p-3 text-xs transition-all ${
									hoveredRegionId === region.id
										? 'border-[#b23a2e]/50 bg-[#b23a2e]/5 dark:border-[#e08a63]/40 dark:bg-[#e08a63]/5'
										: 'border-black/10 bg-black/[0.02] dark:border-white/10 dark:bg-white/[0.02] hover:border-black/20 dark:hover:border-white/20'
								}`}
								on:mouseenter={() => {
									hoveredRegionId = region.id;
									if (inspectTab !== 'bbox') inspectTab = 'bbox';
								}}
								on:mouseleave={() => (hoveredRegionId = null)}
							>
								<!-- HEADER ROW: sequence badge + confidence + box size -->
								<div class="flex items-center justify-between">
									<span class="rounded px-1.5 py-0.5 text-[10px] font-bold text-[#b23a2e] bg-[#b23a2e]/10 dark:text-[#e08a63]">
										#{region.seq + 1}
									</span>
									<div class="flex items-center gap-2 font-mono text-[10px] opacity-50">
										{#if region.conf !== null}
											<span>{(region.conf * 100).toFixed(0)}% conf</span>
										{/if}
										{#if b}
											<span>{b.w}×{b.h}</span>
										{/if}
									</div>
								</div>

								{#if b}
									<div class="mt-1 font-mono text-[9px] opacity-30">
										({b.x}, {b.y}) {b.w}×{b.h} px
									</div>
								{/if}

								<!-- SOURCE OCR -->
								<div class="mt-2">
									<div class="mb-0.5 text-[10px] opacity-50">Source OCR</div>
									<div class="flex items-start gap-1">
										<span class="flex-1 break-words font-mono leading-snug">
											{region.textSource || '—'}
										</span>
										{#if region.textSource}
											<button
												type="button"
												title="Copy source text"
												class="mt-0.5 flex-shrink-0 rounded p-0.5 opacity-30 transition hover:bg-black/10 hover:opacity-80 dark:hover:bg-white/10"
												on:click={() => navigator.clipboard?.writeText(region.textSource)}
											>📋</button>
										{/if}
									</div>
								</div>

								<!-- DEEPSEEK TARGET -->
								{#if region.textTarget}
									<div class="mt-2 border-t border-black/[0.05] pt-1.5 dark:border-white/[0.05]">
										<div class="mb-0.5 text-[10px] font-semibold text-[#b23a2e] dark:text-[#e08a63]">
											DeepSeek Translation
										</div>
										<div class="flex items-start gap-1">
											<span class="flex-1 break-words leading-snug">{region.textTarget}</span>
											<button
												type="button"
												title="Copy translation"
												class="mt-0.5 flex-shrink-0 rounded p-0.5 opacity-30 transition hover:bg-black/10 hover:opacity-80 dark:hover:bg-white/10"
												on:click={() => navigator.clipboard?.writeText(region.textTarget ?? '')}
											>📋</button>
										</div>
									</div>
								{/if}
							</div>
						{/each}
					</div>
				{/if}
			</div>
		</div>
	{/if}

	<svelte:fragment slot="footer">
		{#if page}
			<Button variant="secondary" on:click={copyInspectDebugInfo}>
				<Copy size={14} class="mr-1.5" />
				Copy Debug Data
			</Button>
		{/if}
		<Button on:click={() => dispatch('close')}>Close</Button>
	</svelte:fragment>
</Modal>
