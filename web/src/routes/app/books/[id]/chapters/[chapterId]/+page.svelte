<script lang="ts">
	// IMPORTED DEP-COMPONENTS
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { page } from '$app/stores';
	import { Badge, Button, Modal, ConfirmDialog } from '$lib/components/ui';
	import { ripple } from '$lib/actions/ripple';
	import { streamSse } from '$lib/sse';
	// IMPORTED ICONS
	import ArrowLeft from 'lucide-svelte/icons/arrow-left';
	import Upload from 'lucide-svelte/icons/upload';
	import Download from 'lucide-svelte/icons/download';
	import Play from 'lucide-svelte/icons/play';
	import RefreshCw from 'lucide-svelte/icons/refresh-cw';
	import LayoutGrid from 'lucide-svelte/icons/layout-grid';
	import BookOpen from 'lucide-svelte/icons/book-open';
	import Columns from 'lucide-svelte/icons/columns';
	import Eye from 'lucide-svelte/icons/eye';
	import Trash2 from 'lucide-svelte/icons/trash-2';
	import Sparkles from 'lucide-svelte/icons/sparkles';
	import Layers from 'lucide-svelte/icons/layers';
	import ZoomIn from 'lucide-svelte/icons/zoom-in';
	import ZoomOut from 'lucide-svelte/icons/zoom-out';
	import ArrowUp from 'lucide-svelte/icons/arrow-up';
	import GripVertical from 'lucide-svelte/icons/grip-vertical';
	import { settings } from '$lib/stores/settings';

	// -- TYPES -- //

	interface Region {
		id: number;
		seq: number;
		box: { x?: number; y?: number; w?: number; h?: number } | unknown;
		category: string;
		textSource: string;
		textTarget: string | null;
		conf: number | null;
	}

	interface Page {
		id: number;
		seq: number;
		filePath: string;
		cleanedPath: string | null;
		outputPath: string | null;
		status: 'pending' | 'processing' | 'done' | 'error';
		error: string | null;
		width?: number;
		height?: number;
		regions: Region[];
	}

	// -- STATES -- //

	let pages: Page[] = [];
	let loading = true;
	let running = false;
	let progress = 0;
	let pageCount = 0;
	let selectedFile: FileList | null = null;
	let uploading = false;
	let isDraggingOver = false;

	// PERSISTENT READER CONFIGURATIONS (SYNCED WITH $settings)
	$: activeViewMode = $settings.readerViewMode;
	$: webtoonKind = $settings.webtoonKind;
	$: webtoonWidth = $settings.webtoonWidth;

	function setViewMode(mode: 'reader' | 'grid' | 'compare') {
		settings.update((s) => ({ ...s, readerViewMode: mode }));
	}

	function setWebtoonKind(kind: 'output' | 'original') {
		settings.update((s) => ({ ...s, webtoonKind: kind }));
	}

	function setWebtoonWidth(width: 'sm' | 'md' | 'lg') {
		settings.update((s) => ({ ...s, webtoonWidth: width }));
	}

	const widthClasses = {
		sm: 'max-w-lg',
		md: 'max-w-2xl',
		lg: 'max-w-4xl',
	};

	// PER-PAGE ORIGINAL / OUTPUT TOGGLE IN GRID MODE
	let viewMode = new Map<number, 'original' | 'output'>();

	// REGION INSPECTOR MODAL
	let inspectPage: Page | null = null;
	let inspectModalOpen = false;
	let inspectTab: 'output' | 'cleaned' | 'original' | 'bbox' = 'output';
	/** Seq of the region card currently hovered — highlighted in the Region Map overlay. */
	let hoveredRegionId: number | null = null;

	// PAGE DELETION
	let pageToDelete: Page | null = null;
	let deletePageConfirmOpen = false;
	let deletingPage = false;
	let reloadKey = Date.now();

	// -- LIFECYCLES -- //

	onMount(() => {
		reload();
	});

	// -- FUNCTIONS -- //

	async function reload() {
		try {
			const resp = await fetch(`/api/chapters/${$page.params.chapterId}`);
			if (!resp.ok) throw new Error('load failed');
			const data = await resp.json();
			pages = data.pages;
			reloadKey = Date.now();
		} catch {
			toast.error('Could not load the chapter.');
		} finally {
			loading = false;
		}
	}

	async function uploadFiles(files: FileList | File[]) {
		if (!files || files.length === 0) return;
		uploading = true;
		try {
			const form = new FormData();
			for (const file of Array.from(files)) form.append('files', file);
			const resp = await fetch(`/api/chapters/${$page.params.chapterId}/pages`, { method: 'POST', body: form });
			if (!resp.ok) throw new Error('upload failed');
			const { added } = await resp.json();
			toast.success(`${added} page${added === 1 ? '' : 's'} uploaded.`);
			selectedFile = null;
			await reload();
		} catch {
			toast.error('Upload failed.');
		} finally {
			uploading = false;
		}
	}

	async function upload() {
		if (selectedFile) await uploadFiles(selectedFile);
	}

	function handleDrop(e: DragEvent) {
		e.preventDefault();
		e.stopPropagation();
		isDraggingOver = false;
		if (draggedPageIndex !== null) return;
		if (e.dataTransfer?.types?.includes('application/x-manua-page-id')) return;
		if (e.dataTransfer?.files && e.dataTransfer.files.length > 0) {
			uploadFiles(e.dataTransfer.files);
		}
	}

	function handleDragOver(e: DragEvent) {
		if (draggedPageIndex !== null) return;
		if (e.dataTransfer?.types?.includes('application/x-manua-page-id')) return;
		const isFileDrag = e.dataTransfer?.types?.includes('Files');
		if (!isFileDrag) return;
		e.preventDefault();
		isDraggingOver = true;
	}

	function handleDragLeave(e: DragEvent) {
		if (draggedPageIndex !== null) return;
		e.preventDefault();
		isDraggingOver = false;
	}

	async function translate(force = false) {
		running = true;
		progress = 0;
		const customWatermarks = $settings.watermarkRemoval && $settings.customWatermarks
			? $settings.customWatermarks.split(',').map((s) => s.trim()).filter(Boolean)
			: [];
		try {
			await streamSse(
				`/api/chapters/${$page.params.chapterId}/translate`,
				{ force, watermarkRemoval: $settings.watermarkRemoval, customWatermarks },
				(e) => {
					if (e.type === 'page-done') {
						progress = (e.page as number) + 1;
						pageCount = e.pageCount as number;
					} else if (e.type === 'error') {
						toast.error(String(e.message ?? 'A page failed — see its badge.'));
					}
				},
			);
			await reload();
		} catch (e) {
			toast.error(e instanceof Error ? e.message : 'Translation failed to start.');
		} finally {
			running = false;
		}
	}

	function downloadZip() {
		window.location.href = `/api/chapters/${$page.params.chapterId}/download`;
	}

	const statusLabel: Record<Page['status'], string> = {
		pending: 'Pending',
		processing: 'Processing…',
		done: 'Done',
		error: 'Error',
	};

	const statusVariant: Record<Page['status'], 'neutral' | 'amber' | 'jade' | 'cinnabar'> = {
		pending: 'neutral',
		processing: 'amber',
		done: 'jade',
		error: 'cinnabar',
	};

	function toggleView(pageId: number) {
		viewMode = new Map(viewMode);
		viewMode.set(pageId, viewMode.get(pageId) === 'output' ? 'original' : 'output');
	}

	function openInspector(pg: Page, tab?: 'output' | 'cleaned' | 'original' | 'bbox') {
		inspectPage = pg;
		inspectTab = tab ?? (pg.outputPath ? 'output' : 'original');
		hoveredRegionId = null;
		inspectModalOpen = true;
	}

	function promptDeletePage(pg: Page) {
		pageToDelete = pg;
		deletePageConfirmOpen = true;
	}

	async function confirmDeletePage() {
		if (!pageToDelete) return;
		deletingPage = true;
		try {
			const resp = await fetch(`/api/pages/${pageToDelete.id}`, { method: 'DELETE' });
			if (!resp.ok) throw new Error('Delete failed');
			toast.success(`Deleted page ${pageToDelete.seq + 1}.`);
			pages = pages.filter((p) => p.id !== pageToDelete?.id);
		} catch {
			toast.error('Could not delete page.');
		} finally {
			deletingPage = false;
			deletePageConfirmOpen = false;
			pageToDelete = null;
		}
	}

	let stitchingPageId: number | null = null;
	async function stitchPageWithNext(pageId: number, seq: number) {
		stitchingPageId = pageId;
		try {
			const resp = await fetch(`/api/pages/${pageId}/stitch`, { method: 'POST' });
			if (!resp.ok) {
				const err = await resp.json().catch(() => ({ message: 'Stitch failed' }));
				throw new Error(err.message || 'Stitch failed');
			}
			toast.success(`Stitched page ${seq + 1} with page ${seq + 2}.`);
			await reload();
		} catch (e) {
			toast.error((e as Error).message || 'Could not stitch pages.');
		} finally {
			stitchingPageId = null;
		}
	}

	let currentScrollPage = 1;


	function updateCurrentPageOnScroll() {
		if (activeViewMode !== 'reader' || pages.length === 0 || typeof document === 'undefined') return;
		const pageEls = document.querySelectorAll('[data-page-seq]');
		for (let i = 0; i < pageEls.length; i++) {
			const rect = pageEls[i].getBoundingClientRect();
			if (rect.top <= window.innerHeight / 2 && rect.bottom >= window.innerHeight / 2) {
				currentScrollPage = i + 1;
				break;
			}
		}
	}

	// DRAG & DROP PAGE RE-ORDERING
	let draggedPageIndex: number | null = null;
	let dragOverPageIndex: number | null = null;

	function onPageDragStart(e: DragEvent, index: number) {
		e.stopPropagation();
		draggedPageIndex = index;
		if (e.dataTransfer) {
			e.dataTransfer.effectAllowed = 'move';
			e.dataTransfer.setData('application/x-manua-page-id', String(pages[index].id));
			e.dataTransfer.setData('text/plain', String(index));
		}
	}

	function onPageDragOver(e: DragEvent, index: number) {
		e.preventDefault();
		e.stopPropagation();
		if (draggedPageIndex !== null && draggedPageIndex !== index) {
			dragOverPageIndex = index;
		}
	}

	function onPageDragEnd(e?: DragEvent) {
		if (e) e.stopPropagation();
		draggedPageIndex = null;
		dragOverPageIndex = null;
	}

	async function onPageDrop(e: DragEvent, dropIndex: number) {
		e.preventDefault();
		e.stopPropagation();
		if (draggedPageIndex === null || draggedPageIndex === dropIndex) {
			onPageDragEnd();
			return;
		}

		const updated = [...pages];
		const [moved] = updated.splice(draggedPageIndex, 1);
		updated.splice(dropIndex, 0, moved);
		pages = updated.map((p, idx) => ({ ...p, seq: idx }));

		onPageDragEnd();
		await savePageOrder(pages.map((p) => p.id));
	}

	async function savePageOrder(pageIds: number[]) {
		try {
			const resp = await fetch(`/api/chapters/${$page.params.chapterId}/pages`, {
				method: 'PUT',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ pageIds }),
			});
			if (!resp.ok) throw new Error('reorder failed');
			toast.success('Page sequence updated.');
		} catch {
			toast.error('Could not save page sequence.');
			await reload();
		}
	}

	$: totalRegions = pages.reduce((sum, p) => sum + p.regions.length, 0);
	$: translatedPages = pages.filter((p) => p.status === 'done').length;

	/** Extract typed box coords from the region.box field (typed as unknown in the interface). */
	function getBox(raw: unknown): { x: number; y: number; w: number; h: number } | null {
		if (!raw || typeof raw !== 'object') return null;
		const b = raw as Record<string, unknown>;
		if (!('x' in b)) return null;
		return {
			x: Number(b.x ?? 0),
			y: Number(b.y ?? 0),
			w: Number(b.w ?? 0),
			h: Number(b.h ?? 0),
		};
	}
</script>

<svelte:head>
	<title>Portrait Webtoon Reader — Manua Translator</title>
</svelte:head>

<svelte:window on:scroll={updateCurrentPageOnScroll} />

<!-- CHAPTER WORKSPACE CONTAINER WITH DROPZONE -->
<!-- svelte-ignore a11y-no-static-element-interactions -->
<div
	class="relative flex flex-col gap-6 py-6"
	on:dragover={handleDragOver}
	on:dragleave={handleDragLeave}
	on:drop={handleDrop}
>
	<!-- DRAG & DROP OVERLAY -->
	{#if isDraggingOver}
		<div class="pointer-events-none fixed inset-0 z-50 flex items-center justify-center bg-[#b23a2e]/20 backdrop-blur-sm">
			<div class="flex flex-col items-center rounded-2xl bg-white p-8 shadow-2xl dark:bg-[#1a1713]">
				<Upload size={48} class="animate-bounce text-[#b23a2e]" />
				<h2 class="mt-4 text-lg font-bold">Drop Chapter Images Here</h2>
				<p class="mt-1 text-xs opacity-60">PNG, JPEG, WebP, AVIF files accepted</p>
			</div>
		</div>
	{/if}

	<!-- BREADCRUMB & TOP CONTROLS -->
	<div class="flex items-center justify-between">
		<a
			href={`/app/books/${$page.params.id}`}
			class="inline-flex items-center gap-1.5 text-xs font-semibold opacity-60 transition hover:opacity-100 hover:text-[#b23a2e]"
			use:ripple
		>
			<ArrowLeft size={14} /> Back to Book
		</a>

		<div class="flex items-center gap-2">
			<input
				type="file"
				accept="image/png,image/jpeg,image/webp,image/avif"
				multiple
				class="hidden"
				id="page-files"
				on:change={(e) => {
					selectedFile = e.currentTarget.files;
					if (selectedFile) upload();
				}}
			/>

			<label
				for="page-files"
				class="inline-flex cursor-pointer items-center justify-center gap-1.5 rounded-lg border border-black/10 bg-white/80 px-3 py-1.5 text-xs font-medium transition hover:bg-black/5 dark:border-white/10 dark:bg-white/10 dark:hover:bg-white/15"
				use:ripple
			>
				<Upload size={14} />
				<span>{uploading ? 'Uploading…' : 'Add Images'}</span>
			</label>

			<Button variant="primary" size="sm" disabled={running || pages.length === 0} on:click={() => translate(false)}>
				<Play size={14} /> {running ? 'Translating…' : 'Translate'}
			</Button>
		</div>
	</div>

	<!-- WORKSPACE TOOLBAR & LAYOUT MODE PICKER -->
	<div class="flex flex-col gap-4 rounded-2xl border border-black/[0.08] bg-white/50 p-4 backdrop-blur dark:border-white/[0.06] dark:bg-white/[0.02]">
		<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
			<div>
				<div class="flex items-center gap-2">
					<h1 class="text-xl font-bold tracking-tight">Portrait Webtoon Reader</h1>
					<span class="rounded-full bg-[#b23a2e]/10 px-2.5 py-0.5 text-xs font-semibold text-[#b23a2e] dark:text-[#e08a63]">
						{translatedPages}/{pages.length} Pages Translated
					</span>
				</div>
				<p class="mt-0.5 text-xs opacity-60">
					Continuous vertical portrait strip designed specifically for Chinese Manhua & Webtoons.
				</p>
			</div>

			<div class="flex items-center gap-2">
				<Button variant="secondary" size="sm" disabled={!running} on:click={() => translate(true)}>
					<RefreshCw size={14} /> Force Re-run
				</Button>

				<Button variant="secondary" size="sm" disabled={pages.length === 0} on:click={downloadZip}>
					<Download size={14} /> Export Zip
				</Button>
			</div>
		</div>

		<!-- VIEW MODE & WEBTOON CONTROLS BAR -->
		{#if pages.length > 0}
			<div class="flex flex-wrap items-center justify-between gap-3 border-t border-black/[0.04] pt-3 text-xs dark:border-white/[0.04]">
				<!-- MODE SWITCHER -->
				<div class="flex items-center gap-1 rounded-lg bg-black/5 p-1 dark:bg-white/5">
					<button
						type="button"
						class={`flex items-center gap-1.5 rounded-md px-3 py-1 font-medium transition ${
							activeViewMode === 'reader'
								? 'bg-white shadow text-[#b23a2e] dark:bg-[#1a1713] dark:text-[#e08a63]'
								: 'opacity-60 hover:opacity-100'
						}`}
						on:click={() => setViewMode('reader')}
					>
						<BookOpen size={13} /> Portrait Webtoon
					</button>

					<button
						type="button"
						class={`flex items-center gap-1.5 rounded-md px-3 py-1 font-medium transition ${
							activeViewMode === 'grid'
								? 'bg-white shadow text-[#b23a2e] dark:bg-[#1a1713] dark:text-[#e08a63]'
								: 'opacity-60 hover:opacity-100'
						}`}
						on:click={() => setViewMode('grid')}
					>
						<LayoutGrid size={13} /> Grid View
					</button>

					<button
						type="button"
						class={`flex items-center gap-1.5 rounded-md px-3 py-1 font-medium transition ${
							activeViewMode === 'compare'
								? 'bg-white shadow text-[#b23a2e] dark:bg-[#1a1713] dark:text-[#e08a63]'
								: 'opacity-60 hover:opacity-100'
						}`}
						on:click={() => setViewMode('compare')}
					>
						<Columns size={13} /> Side-by-Side
					</button>
				</div>

				<!-- READER-SPECIFIC TOGGLES -->
				{#if activeViewMode === 'reader'}
					<div class="flex items-center gap-3">
						<!-- STRIP WIDTH PICKER -->
						<div class="flex items-center gap-1 rounded-lg bg-black/5 p-1 dark:bg-white/5">
							<button
								type="button"
								class={`rounded px-2 py-0.5 font-medium text-[11px] ${webtoonWidth === 'sm' ? 'bg-white shadow dark:bg-[#1a1713]' : 'opacity-60'}`}
								on:click={() => setWebtoonWidth('sm')}
							>
								Compact
							</button>
							<button
								type="button"
								class={`rounded px-2 py-0.5 font-medium text-[11px] ${webtoonWidth === 'md' ? 'bg-white shadow dark:bg-[#1a1713]' : 'opacity-60'}`}
								on:click={() => setWebtoonWidth('md')}
							>
								Standard
							</button>
							<button
								type="button"
								class={`rounded px-2 py-0.5 font-medium text-[11px] ${webtoonWidth === 'lg' ? 'bg-white shadow dark:bg-[#1a1713]' : 'opacity-60'}`}
								on:click={() => setWebtoonWidth('lg')}
							>
								Wide
							</button>
						</div>

						<!-- ORIGINAL / TRANSLATED STRIP SWITCH -->
						<div class="flex items-center gap-1 rounded-lg bg-black/5 p-1 dark:bg-white/5">
							<button
								type="button"
								class={`rounded px-2.5 py-0.5 font-medium text-[11px] ${webtoonKind === 'output' ? 'bg-[#b23a2e] text-white' : 'opacity-60'}`}
								on:click={() => setWebtoonKind('output')}
							>
								Translated
							</button>
							<button
								type="button"
								class={`rounded px-2.5 py-0.5 font-medium text-[11px] ${webtoonKind === 'original' ? 'bg-white shadow text-current dark:bg-[#1a1713]' : 'opacity-60'}`}
								on:click={() => setWebtoonKind('original')}
							>
								Original
							</button>
						</div>
					</div>
				{/if}
			</div>
		{/if}
	</div>

	<!-- SSE PROGRESS BAR -->
	{#if running}
		<div class="rounded-xl border border-[#b23a2e]/20 bg-[#b23a2e]/5 p-4">
			<div class="mb-2 flex items-center justify-between text-xs font-semibold">
				<span class="flex items-center gap-2 text-[#b23a2e] dark:text-[#e08a63]">
					<Sparkles size={14} class="animate-spin" /> DeepSeek & ML Pipeline Translating...
				</span>
				<span>{progress} of {pageCount || pages.length} pages done</span>
			</div>
			<div class="h-2.5 w-full overflow-hidden rounded-full bg-black/10 dark:bg-white/10">
				<div
					class="h-full rounded-full bg-[#b23a2e] transition-all duration-300"
					style="width: {pageCount ? (progress / pageCount) * 100 : 0}%"
				></div>
			</div>
		</div>
	{/if}

	<!-- CONTENT VIEWS -->
	{#if loading}
		<div class="flex flex-col items-center gap-2">
			{#each [1, 2] as _}
				<div class="h-96 w-full max-w-2xl animate-pulse rounded-xl border border-black/[0.06] bg-black/[0.03] dark:border-white/[0.06] dark:bg-white/[0.03]"></div>
			{/each}
		</div>
	{:else if pages.length === 0}
		<div class="flex flex-col items-center justify-center rounded-2xl border border-dashed border-black/15 py-16 text-center dark:border-white/15">
			<div class="flex h-12 w-12 items-center justify-center rounded-full bg-[#b23a2e]/10 text-[#b23a2e] dark:text-[#e08a63]">
				<Upload size={24} />
			</div>
			<h2 class="mt-4 text-base font-semibold">No chapter pages uploaded yet</h2>
			<p class="mt-1 max-w-sm text-xs opacity-60">Drag and drop manhua page images here or click 'Add Images' above.</p>
		</div>

	{:else if activeViewMode === 'reader'}
		<!-- PRIMARY PORTRAIT WEBTOON SCROLLER -->
		<div class="flex flex-col items-center w-full">
			<div class={`relative w-full ${widthClasses[webtoonWidth]} overflow-hidden rounded-2xl border border-black/10 bg-black shadow-2xl transition-all duration-300 dark:border-white/10`}>
				<!-- CONTINUOUS PORTRAIT WEBTOON STRIP (100% BORDERLESS SEAMLESS CUTS) -->
				<div class="flex flex-col items-center w-full bg-black p-0 m-0 leading-none">
					{#each pages as page, idx (page.id)}
						<div
							draggable="true"
							on:dragstart={(e) => onPageDragStart(e, idx)}
							on:dragover={(e) => onPageDragOver(e, idx)}
							on:drop={(e) => onPageDrop(e, idx)}
							on:dragend={onPageDragEnd}
							class={`group relative w-full border-0 p-0 m-0 leading-none bg-black transition-all ${
								dragOverPageIndex === idx ? 'ring-4 ring-[#b23a2e] z-10 scale-[1.01]' : ''
							} ${draggedPageIndex === idx ? 'opacity-40' : ''}`}
							data-page-seq={page.seq}
						>
							<!-- IMAGE -->
							<img
								src={`/api/pages/${page.id}/file?kind=${webtoonKind === 'output' && page.outputPath ? 'output' : 'original'}&v=${reloadKey}`}
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
								<Badge variant={statusVariant[page.status]}>
									{statusLabel[page.status]}
								</Badge>
							</div>

							<div class="absolute bottom-3 right-3 flex items-center gap-1.5 opacity-0 transition-opacity duration-200 group-hover:opacity-100">
								{#if idx < pages.length - 1}
									<button
										type="button"
										on:click={() => stitchPageWithNext(page.id, page.seq)}
										disabled={stitchingPageId === page.id}
										class="flex items-center gap-1 rounded-md bg-blue-600/80 px-2.5 py-1 text-[11px] font-semibold text-white backdrop-blur transition hover:bg-blue-600 pointer-events-auto disabled:opacity-50"
										title="Stitch with page {page.seq + 2}"
									>
										{stitchingPageId === page.id ? 'Stitching...' : 'Merge Next'}
									</button>
								{/if}
								<button
									type="button"
									on:click={() => openInspector(page)}
									class="flex items-center gap-1 rounded-md bg-black/80 px-2.5 py-1 text-[11px] font-semibold text-white backdrop-blur transition hover:bg-black pointer-events-auto"
								>
									<Eye size={12} /> Inspect
								</button>

								<button
									type="button"
									on:click={() => promptDeletePage(page)}
									class="flex items-center gap-1 rounded-md bg-red-600/80 px-2 py-1 text-[11px] font-semibold text-white backdrop-blur transition hover:bg-red-600 pointer-events-auto"
								>
									<Trash2 size={12} />
								</button>
							</div>
						</div>
					{/each}
				</div>
			</div>

			<!-- FLOATING UNINTRUSIVE WEBTOON DOCK -->
			<div class="fixed bottom-6 right-6 z-40 flex items-center gap-2 rounded-full border border-white/15 bg-black/85 px-4 py-2 text-xs font-semibold text-white shadow-2xl backdrop-blur transition-all duration-300 hover:bg-black">
				<span class="text-[11px] font-mono opacity-80">Page {currentScrollPage} / {pages.length}</span>
				<span class="h-3 w-px bg-white/20"></span>
				<button
					type="button"
					on:click={() => setWebtoonKind(webtoonKind === 'output' ? 'original' : 'output')}
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
		</div>

	{:else if activeViewMode === 'grid'}
		<!-- GRID VIEW MODE -->
		<div class="grid w-full gap-4 sm:grid-cols-2 lg:grid-cols-3">
			{#each pages as page, idx (page.id)}
				<div
					draggable="true"
					on:dragstart={(e) => onPageDragStart(e, idx)}
					on:dragover={(e) => onPageDragOver(e, idx)}
					on:drop={(e) => onPageDrop(e, idx)}
					on:dragend={onPageDragEnd}
					class={`group relative flex flex-col justify-between rounded-xl border p-3.5 transition-all ${
						dragOverPageIndex === idx
							? 'border-[#b23a2e] ring-2 ring-[#b23a2e]/40 bg-[#b23a2e]/5 scale-[1.02] z-10'
							: 'border-black/[0.08] bg-white/40 hover:border-[#b23a2e]/40 hover:shadow-md dark:border-white/[0.06] dark:bg-white/[0.02]'
					} ${draggedPageIndex === idx ? 'opacity-40 scale-95' : ''}`}
				>
					<div>
						<div class="mb-2 flex items-center justify-between">
							<span class="flex items-center gap-1 cursor-grab text-xs font-bold active:cursor-grabbing">
								<GripVertical size={13} class="opacity-40" /> Page {page.seq + 1}
							</span>
							<div class="flex items-center gap-2">
								<Badge variant={statusVariant[page.status]}>
									{statusLabel[page.status]}
								</Badge>
								{#if idx < pages.length - 1}
									<button
										type="button"
										on:click={() => stitchPageWithNext(page.id, page.seq)}
										disabled={stitchingPageId === page.id}
										class="rounded px-1.5 py-0.5 text-[10px] font-semibold bg-blue-500/10 text-blue-600 hover:bg-blue-500/20 disabled:opacity-50"
										title="Stitch with page {page.seq + 2}"
									>
										{stitchingPageId === page.id ? 'Stitching...' : 'Merge Next'}
									</button>
								{/if}
								<button
									type="button"
									on:click={() => promptDeletePage(page)}
									class="rounded p-1 opacity-40 hover:bg-red-500/10 hover:opacity-100 hover:text-red-600"
									aria-label="Delete Page"
								>
									<Trash2 size={13} />
								</button>
							</div>
						</div>

						<!-- IMAGE CONTAINER -->
						<div class="relative overflow-hidden rounded-lg border border-black/10 bg-black/5 dark:border-white/10">
							{#if page.status === 'done' && page.outputPath}
								<img
									src={`/api/pages/${page.id}/file?kind=${viewMode.get(page.id) === 'output' ? 'output' : 'original'}&v=${reloadKey}`}
									alt={`Page ${page.seq + 1}`}
									draggable="false"
									class="w-full object-cover transition-opacity duration-200 select-none"
								/>
								<button
									type="button"
									class="absolute right-2 top-2 rounded-md border border-black/10 bg-white/85 px-2 py-1 text-[11px] font-semibold backdrop-blur transition hover:bg-white dark:border-white/10 dark:bg-black/70 dark:hover:bg-black/90"
									on:click={() => toggleView(page.id)}
									use:ripple
								>
									{viewMode.get(page.id) === 'output' ? 'Show original' : 'Show translation'}
								</button>
							{:else}
								<img
									src={`/api/pages/${page.id}/file?kind=original&v=${reloadKey}`}
									alt={`Page ${page.seq + 1}`}
									draggable="false"
									class="w-full object-cover select-none"
								/>
							{/if}

							<!-- INSPECTOR TRIGGER OVERLAY -->
							<button
								type="button"
								on:click={() => openInspector(page)}
								class="absolute bottom-2 right-2 flex items-center gap-1 rounded-md bg-black/75 px-2.5 py-1 text-[11px] font-medium text-white backdrop-blur transition hover:bg-black"
							>
								<Eye size={12} /> Inspect
							</button>
						</div>

						{#if page.error}
							<p class="mt-2 text-xs text-red-600 dark:text-red-400">{page.error}</p>
						{/if}

						<!-- REGIONS PREVIEW -->
						{#if page.regions.length > 0}
							<div class="mt-2.5 border-t border-black/[0.04] pt-2 text-[11px] opacity-70 dark:border-white/[0.04]">
								<div class="font-semibold text-xs opacity-80 mb-1">{page.regions.length} detected regions</div>
								<ul class="space-y-0.5 max-h-16 overflow-y-auto">
									{#each page.regions.slice(0, 3) as region}
										<li class="truncate">
											<span class="font-medium text-[#b23a2e] dark:text-[#e08a63]">{region.category}:</span>
											{region.textSource || '—'}
											{#if region.textTarget}<span class="opacity-60">→ {region.textTarget}</span>{/if}
										</li>
									{/each}
									{#if page.regions.length > 3}
										<li class="opacity-40 font-italic">+ {page.regions.length - 3} more...</li>
									{/if}
								</ul>
							</div>
						{/if}
					</div>
				</div>
			{/each}
		</div>

	{:else if activeViewMode === 'compare'}
		<!-- SIDE-BY-SIDE COMPARISON MODE WITH HOVER TOOLS & DRAG REORDERING -->
		<div class="flex flex-col gap-6 w-full">
			{#each pages as page, idx (page.id)}
				<div
					draggable="true"
					on:dragstart={(e) => onPageDragStart(e, idx)}
					on:dragover={(e) => onPageDragOver(e, idx)}
					on:drop={(e) => onPageDrop(e, idx)}
					on:dragend={onPageDragEnd}
					class={`group relative rounded-xl border p-4 transition-all ${
						dragOverPageIndex === idx
							? 'border-[#b23a2e] ring-2 ring-[#b23a2e]/40 bg-[#b23a2e]/5 scale-[1.01] z-10'
							: 'border-black/[0.08] bg-white/40 hover:border-[#b23a2e]/40 hover:shadow-md dark:border-white/[0.06] dark:bg-white/[0.02]'
					} ${draggedPageIndex === idx ? 'opacity-40 scale-95' : ''}`}
				>
					<div class="mb-3 flex items-center justify-between text-xs font-bold">
						<div class="flex items-center gap-2">
							<span class="flex items-center gap-1 cursor-grab active:cursor-grabbing">
								<GripVertical size={14} class="opacity-40" /> Page {page.seq + 1} Side-by-Side Comparison
							</span>
							<Badge variant={statusVariant[page.status]}>{statusLabel[page.status]}</Badge>
						</div>

						<div class="flex items-center gap-1.5">
							{#if idx < pages.length - 1}
								<button
									type="button"
									on:click={() => stitchPageWithNext(page.id, page.seq)}
									disabled={stitchingPageId === page.id}
									class="flex items-center gap-1 rounded-md bg-blue-500/10 px-2 py-1 text-xs font-semibold text-blue-600 hover:bg-blue-500/20 disabled:opacity-50"
									title="Stitch with page {page.seq + 2}"
								>
									{stitchingPageId === page.id ? 'Stitching...' : 'Merge Next'}
								</button>
							{/if}
							<button
								type="button"
								on:click={() => openInspector(page)}
								class="flex items-center gap-1 rounded-md bg-black/5 px-2.5 py-1 text-xs font-semibold hover:bg-black/10 dark:bg-white/5 dark:hover:bg-white/10"
							>
								<Eye size={13} /> Inspect
							</button>

							<button
								type="button"
								on:click={() => promptDeletePage(page)}
								class="flex items-center gap-1 rounded-md bg-red-500/10 px-2 py-1 text-xs font-semibold text-red-600 hover:bg-red-500/20"
								aria-label="Delete Page"
							>
								<Trash2 size={13} />
							</button>
						</div>
					</div>

					<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
						<!-- ORIGINAL PAGE COLUMN WITH HOVER TOOLS -->
						<div class="flex flex-col gap-1.5">
							<div class="flex items-center justify-between text-xs font-semibold opacity-60">
								<span>Original Page</span>
							</div>
							<div class="group/img relative overflow-hidden rounded-lg border border-black/10 bg-black/5 dark:border-white/10">
								<img
									src={`/api/pages/${page.id}/file?kind=original&v=${reloadKey}`}
									alt={`Page ${page.seq + 1} Original`}
									draggable="false"
									class="w-full h-auto block object-contain select-none"
								/>
								<!-- HOVER-ONLY OVERLAY TOOLS -->
								<div class="absolute bottom-2 left-2 flex items-center gap-1.5 opacity-0 transition-opacity duration-200 group-hover/img:opacity-100">
									<span class="rounded bg-black/80 px-2 py-0.5 text-[10px] font-bold text-white backdrop-blur">
										Original
									</span>
								</div>
								<div class="absolute bottom-2 right-2 flex items-center gap-1.5 opacity-0 transition-opacity duration-200 group-hover/img:opacity-100">
									<button
										type="button"
										on:click={() => openInspector(page, 'original')}
										class="flex items-center gap-1 rounded-md bg-black/80 px-2.5 py-1 text-[11px] font-semibold text-white backdrop-blur transition hover:bg-black"
									>
										<Eye size={12} /> Inspect
									</button>
									<button
										type="button"
										on:click={() => promptDeletePage(page)}
										class="flex items-center gap-1 rounded-md bg-red-600/80 px-2 py-1 text-[11px] font-semibold text-white backdrop-blur transition hover:bg-red-600"
									>
										<Trash2 size={12} />
									</button>
								</div>
							</div>
						</div>

						<!-- TRANSLATED / CLEANED OUTPUT COLUMN WITH HOVER TOOLS -->
						<div class="flex flex-col gap-1.5">
							<div class="flex items-center justify-between text-xs font-semibold opacity-60">
								<span>Translated / Cleaned Output</span>
							</div>
							{#if page.outputPath}
								<div class="group/img relative overflow-hidden rounded-lg border border-black/10 bg-black/5 dark:border-white/10">
									<img
										src={`/api/pages/${page.id}/file?kind=output&v=${reloadKey}`}
										alt={`Page ${page.seq + 1} Output`}
										draggable="false"
										class="w-full h-auto block object-contain select-none"
									/>
									<!-- HOVER-ONLY OVERLAY TOOLS -->
									<div class="absolute bottom-2 left-2 flex items-center gap-1.5 opacity-0 transition-opacity duration-200 group-hover/img:opacity-100">
										<span class="rounded bg-black/80 px-2 py-0.5 text-[10px] font-bold text-white backdrop-blur">
											Translated
										</span>
									</div>
									<div class="absolute bottom-2 right-2 flex items-center gap-1.5 opacity-0 transition-opacity duration-200 group-hover/img:opacity-100">
										<button
											type="button"
											on:click={() => openInspector(page, 'output')}
											class="flex items-center gap-1 rounded-md bg-black/80 px-2.5 py-1 text-[11px] font-semibold text-white backdrop-blur transition hover:bg-black"
										>
											<Eye size={12} /> Inspect
										</button>
										<button
											type="button"
											on:click={() => promptDeletePage(page)}
											class="flex items-center gap-1 rounded-md bg-red-600/80 px-2 py-1 text-[11px] font-semibold text-white backdrop-blur transition hover:bg-red-600"
										>
											<Trash2 size={12} />
										</button>
									</div>
								</div>
							{:else}
								<div class="flex h-64 items-center justify-center rounded-lg border border-dashed border-black/20 text-xs opacity-50 dark:border-white/20">
									Translation not completed yet
								</div>
							{/if}
						</div>
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>

<!-- PAGE REGION INSPECTOR MODAL -->
<Modal
	open={inspectModalOpen}
	title={inspectPage ? `Page ${inspectPage.seq + 1} Region Inspector` : 'Inspector'}
	size="xl"
	on:close={() => {
		inspectModalOpen = false;
		hoveredRegionId = null;
	}}
>
	{#if inspectPage}
		{@const pw = inspectPage.width}
		{@const ph = inspectPage.height}
		<div class="grid grid-cols-1 gap-6 lg:grid-cols-12">
			<!-- IMAGE / OVERLAY COLUMN -->
			<div class="flex flex-col gap-3 lg:col-span-7">
				<!-- TAB STRIP -->
				<div class="flex flex-wrap items-center gap-1.5 text-xs">
					{#if inspectPage.outputPath}
						<button
							type="button"
							class={`rounded-lg px-3 py-1.5 font-medium transition ${inspectTab === 'output' ? 'bg-[#b23a2e] text-white' : 'bg-black/5 dark:bg-white/5 hover:bg-black/10 dark:hover:bg-white/10'}`}
							on:click={() => (inspectTab = 'output')}
						>
							Typeset Output
						</button>
					{/if}

					{#if inspectPage.cleanedPath}
						<button
							type="button"
							class={`rounded-lg px-3 py-1.5 font-medium transition ${inspectTab === 'cleaned' ? 'bg-[#b23a2e] text-white' : 'bg-black/5 dark:bg-white/5 hover:bg-black/10 dark:hover:bg-white/10'}`}
							on:click={() => (inspectTab = 'cleaned')}
						>
							LaMa Cleaned
						</button>
					{/if}

					<!-- BUG FIX: was `text-[#b23a2e]` (red text on red bg = invisible). Now `text-white`. -->
					<button
						type="button"
						class={`rounded-lg px-3 py-1.5 font-medium transition ${inspectTab === 'original' ? 'bg-[#b23a2e] text-white' : 'bg-black/5 dark:bg-white/5 hover:bg-black/10 dark:hover:bg-white/10'}`}
						on:click={() => (inspectTab = 'original')}
					>
						Original Image
					</button>

					<!-- REGION MAP — detection bounding boxes overlaid on the original -->
					<button
						type="button"
						class={`rounded-lg px-3 py-1.5 font-medium transition ${inspectTab === 'bbox' ? 'bg-emerald-600 text-white' : 'bg-black/5 dark:bg-white/5 hover:bg-black/10 dark:hover:bg-white/10'}`}
						on:click={() => (inspectTab = 'bbox')}
					>
						🎯 Region Map
					</button>
				</div>

				<!-- PANEL: bbox overlay -->
				{#if inspectTab === 'bbox'}
					<div class="relative overflow-hidden rounded-xl border border-black/10 bg-black/5 dark:border-white/10">
						<img
							src={`/api/pages/${inspectPage.id}/file?kind=original&v=${reloadKey}`}
							alt={`Page ${inspectPage.seq + 1} original`}
							class="block h-auto w-full object-contain"
							style="max-height: 60vh;"
						/>
						{#if pw && ph}
							<!-- SVG coord space = original pixel dimensions; preserveAspectRatio mirrors object-contain -->
							<svg
								class="pointer-events-none absolute inset-0 h-full w-full"
								viewBox="0 0 {pw} {ph}"
								preserveAspectRatio="xMidYMid meet"
								xmlns="http://www.w3.org/2000/svg"
							>
								{#each inspectPage.regions as region (region.id)}
									{@const b = getBox(region.box)}
									{@const bx = b?.x ?? 0}
									{@const by = b?.y ?? 0}
									{@const bw = b?.w ?? 0}
									{@const bh = b?.h ?? 0}
									{@const stroke =
										region.category === 'sfx'
											? '#f97316'
											: region.category === 'mono'
												? '#3b82f6'
												: region.category === 'other'
													? '#9ca3af'
													: '#ef4444'}
									{@const active = hoveredRegionId === region.id}
									<!-- Bounding box rect -->
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
									<!-- Region label: outlined text for readability on any background -->
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
									<!-- Category label -->
									<text
										x={bx + 6}
										y={by + 38}
										font-size="13"
										fill={stroke}
										stroke="#000"
										stroke-width="3"
										paint-order="stroke"
										opacity="0.9"
									>{region.category}</text>
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

				<!-- PANEL: standard image tab -->
				{:else}
					<div class="overflow-hidden rounded-xl border border-black/10 bg-black/5 dark:border-white/10">
						<img
							src={`/api/pages/${inspectPage.id}/file?kind=${inspectTab}&v=${reloadKey}`}
							alt={`Page ${inspectPage.seq + 1}`}
							class="block h-auto w-full object-contain"
							style="max-height: 60vh;"
						/>
					</div>
				{/if}

				<!-- PAGE DIMENSIONS CHIP -->
				{#if pw && ph}
					<p class="text-[10px] opacity-40 font-mono">{pw} × {ph} px · {inspectPage.regions.length} regions</p>
				{/if}
			</div>

			<!-- REGIONS LIST COLUMN -->
			<div class="flex flex-col gap-3 lg:col-span-5">
				<div class="flex items-center justify-between gap-2">
					<h3 class="text-sm font-bold">
						Detected Regions ({inspectPage.regions.length})
					</h3>
					<!-- CATEGORY LEGEND -->
					{#if inspectPage.regions.length > 0}
						<div class="flex flex-wrap items-center gap-1 text-[9px] font-bold">
							<span class="rounded px-1.5 py-0.5 bg-red-500/10 text-red-600">● dialogue</span>
							<span class="rounded px-1.5 py-0.5 bg-orange-500/10 text-orange-600">● sfx</span>
							<span class="rounded px-1.5 py-0.5 bg-blue-500/10 text-blue-600">● mono</span>
							<span class="rounded px-1.5 py-0.5 bg-gray-500/10 text-gray-500">● other</span>
						</div>
					{/if}
				</div>

				{#if inspectPage.regions.length === 0}
					<p class="text-xs opacity-60">No text regions detected on this page yet.</p>
				{:else}
					<div class="max-h-[60vh] space-y-2 overflow-y-auto pr-1">
						{#each inspectPage.regions as region (region.id)}
							{@const b = getBox(region.box)}
							{@const catCls =
								region.category === 'sfx'
									? 'text-orange-600 bg-orange-500/10'
									: region.category === 'mono'
										? 'text-blue-600 bg-blue-500/10'
										: region.category === 'other'
											? 'text-gray-500 bg-gray-500/10'
											: 'text-[#b23a2e] bg-[#b23a2e]/10 dark:text-[#e08a63]'}
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
								<!-- HEADER ROW: category badge + confidence + box size -->
								<div class="flex items-center justify-between">
									<span class={`rounded px-1.5 py-0.5 text-[10px] font-bold ${catCls}`}>
										#{region.seq + 1} {region.category}
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

								<!-- BOX COORDINATES -->
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
		<Button on:click={() => (inspectModalOpen = false)}>Close</Button>
	</svelte:fragment>
</Modal>


<!-- DELETE PAGE CONFIRMATION -->
<ConfirmDialog
	open={deletePageConfirmOpen}
	title="Delete Page?"
	description={`Are you sure you want to delete Page ${pageToDelete ? pageToDelete.seq + 1 : ''}? This cannot be undone.`}
	confirmLabel="Delete Page"
	destructive
	loading={deletingPage}
	on:confirm={confirmDeletePage}
	on:cancel={() => (deletePageConfirmOpen = false)}
/>
