<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { browser } from '$app/environment';
	import { toast } from 'svelte-sonner';
	import { page } from '$app/stores';
	import { ConfirmDialog } from '$lib/components/ui';
	import { settings } from '$lib/stores/settings';
	import { jobTracker } from '$lib/stores/job-tracker';
	import ChapterToolbar from '$lib/components/chapter/ChapterToolbar.svelte';
	import PipelineProgressTracker from '$lib/components/chapter/PipelineProgressTracker.svelte';
	import ViewModeWebtoon from '$lib/components/chapter/ViewModeWebtoon.svelte';
	import ViewModeGrid from '$lib/components/chapter/ViewModeGrid.svelte';
	import ViewModeCompare from '$lib/components/chapter/ViewModeCompare.svelte';
	import PageInspectModal from '$lib/components/chapter/PageInspectModal.svelte';
	import ResliceModal from '$lib/components/ResliceModal.svelte';
	import Upload from 'lucide-svelte/icons/upload';

	interface Region {
		id: number;
		seq: number;
		box: unknown;
		category: string;
		textSource: string;
		textTarget: string | null;
		conf: number | null;
	}

	interface PageData {
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

	interface ChapterData {
		id: number;
		bookId: string;
		seq: number;
		title: string | null;
		titleTarget?: string | null;
	}

	let chapter: ChapterData | null = null;
	let pages: PageData[] = [];
	let loading = true;
	let uploading = false;
	let isDraggingOver = false;
	let reloadKey = Date.now();

	// MODALS & INSPECTOR
	let inspectPage: PageData | null = null;
	let inspectModalOpen = false;
	let deletePageConfirmOpen = false;
	let pageToDelete: PageData | null = null;
	let clearChapterConfirmOpen = false;
	let resliceModalOpen = false;

	// DRAG & DROP REORDERING STATE
	let draggedPageIndex: number | null = null;
	let dragOverPageIndex: number | null = null;

	$: chapterId = Number($page.params.chapterId);
	$: bookId = $page.params.id;

	// ACTIVE TRANSLATION JOB STATE (SELF-HEALING & REACTIVE)
	$: currentJobState = $jobTracker.jobs[chapterId] || {
		chapterId,
		running: false,
		connectionState: 'idle',
		snapshot: null,
		lastError: null,
		reconnectAttempts: 0,
	};

	// REAL-TIME SYNCHRONIZED PAGES MERGED WITH SNAPSHOT
	$: displayPages = (() => {
		if (!currentJobState.snapshot?.pages?.length) return pages;
		const snapshotPageMap = new Map<number, (typeof currentJobState.snapshot.pages)[0]>();
		for (const sp of currentJobState.snapshot.pages) {
			snapshotPageMap.set(sp.pageId, sp);
		}

		return pages.map((p) => {
			const sp = snapshotPageMap.get(p.id);
			if (!sp) return p;
			const isDone = sp.status === 'done';
			const isError = sp.status === 'error';
			const isProcessing = sp.status === 'processing' && currentJobState.running;
			const status = isDone ? 'done' : isError ? 'error' : isProcessing ? 'processing' : 'pending';

			return {
				...p,
				status,
				currentStep: isProcessing ? sp.currentStep : undefined,
				outputPath: sp.outputPath || p.outputPath,
				error: isError ? sp.errorMessage || p.error : null,
			};
		});
	})();

	// PERSISTENT USER SETTINGS
	$: activeViewMode = $settings.readerViewMode;
	$: webtoonKind = $settings.webtoonKind;
	$: webtoonWidth = $settings.webtoonWidth;

	onMount(async () => {
		await reload();
		// SELF-HEALING: CHECK IF SERVER ALREADY HAS AN ACTIVE TRANSLATION JOB RUNNING
		if (chapterId) {
			await jobTracker.syncChapter(chapterId);
		}
	});

	onDestroy(() => {
		// Do not abort backend job, just detach client SSE stream listener
		if (chapterId) jobTracker.disconnect(chapterId);
	});

	// RELOAD CHAPTER DATA WHEN PROGRESS COMPLETES
	let lastRunning = false;
	$: {
		if (browser && lastRunning && !currentJobState.running) {
			void reload();
		}
		lastRunning = currentJobState.running;
	}

	async function reload() {
		if (!browser) return;
		try {
			const resp = await fetch(`/api/chapters/${chapterId}`);
			if (!resp.ok) throw new Error('Load failed');
			const data = await resp.json();
			chapter = data.chapter;
			pages = data.pages;
			reloadKey = Date.now();
		} catch {
			toast.error('Could not load chapter pages.');
		} finally {
			loading = false;
		}
	}

	async function startTranslation(force = false) {
		const pendingPages = pages.filter((p) => p.status !== 'done');
		if (pendingPages.length === 0 && pages.length > 0 && !force) {
			toast.info('All pages are already translated! Use Clear Progress to reset or translate individual pages.');
			return;
		}

		try {
			await jobTracker.startTranslation(chapterId, { force });
		} catch (e: any) {
			toast.error(e?.message || 'Translation failed to start.');
		}
	}

	async function cancelTranslation() {
		try {
			await jobTracker.cancelTranslation(chapterId);
			toast.info('Translation stopped.');
			await reload();
		} catch {
			toast.error('Failed to cancel translation.');
		}
	}

	async function cancelSinglePage(pg: PageData) {
		try {
			await jobTracker.cancelPage(chapterId, pg.id);
			pg.status = 'pending';
			pg.error = null;
			pages = [...pages];
			toast.info(`Cancelled translation for Page ${pg.seq + 1}.`);
		} catch {
			toast.error(`Could not cancel translation for Page ${pg.seq + 1}.`);
		}
	}

	async function translateSinglePage(pg: PageData) {
		try {
			if (pg.status === 'done') {
				const resetResp = await fetch(`/api/pages/${pg.id}/reset`, { method: 'POST' });
				if (!resetResp.ok) throw new Error('Reset failed');
				pg.status = 'pending';
				pg.outputPath = null;
			}
			pg.error = null;
			pages = [...pages];
			// If a job is already running, don't supersede it — pass force:false so
			// the backend attaches the new page(s) to the existing pipeline instead of
			// aborting it. Only force a fresh start when nothing is currently running.
			const shouldForce = !currentJobState.running;
			await jobTracker.startTranslation(chapterId, { force: shouldForce, pageIds: [pg.id] });
		} catch (e: any) {
			toast.error(e?.message || 'Failed to start single page translation.');
		}
	}

	async function clearPageProgress(pg: PageData) {
		try {
			const resp = await fetch(`/api/pages/${pg.id}/reset`, { method: 'POST' });
			if (!resp.ok) throw new Error('Reset failed');
			pg.status = 'pending';
			pg.cleanedPath = null;
			pg.outputPath = null;
			pg.error = null;
			pages = [...pages];
			if (!currentJobState.running) {
				jobTracker.clearJob(chapterId);
			}
			toast.success(`Cleared progress on Page ${pg.seq + 1}.`);
			reloadKey = Date.now();
		} catch {
			toast.error('Could not clear page progress.');
		}
	}

	async function confirmClearChapterProgress() {
		clearChapterConfirmOpen = false;
		try {
			const resp = await fetch(`/api/chapters/${chapterId}/reset`, { method: 'POST' });
			if (!resp.ok) throw new Error('Reset failed');
			const { reset } = await resp.json();
			jobTracker.clearJob(chapterId);
			toast.success(`Cleared progress on ${reset} page${reset === 1 ? '' : 's'}.`);
			await reload();
		} catch {
			toast.error('Could not clear chapter progress.');
		}
	}

	async function uploadFiles(files: FileList | File[]) {
		if (!files || files.length === 0) return;
		uploading = true;
		try {
			const form = new FormData();
			for (const file of Array.from(files)) form.append('files', file);
			const resp = await fetch(`/api/chapters/${chapterId}/pages`, { method: 'POST', body: form });
			if (!resp.ok) throw new Error('Upload failed');
			const { added } = await resp.json();
			toast.success(`${added} page${added === 1 ? '' : 's'} uploaded.`);
			await reload();
		} catch {
			toast.error('Upload failed.');
		} finally {
			uploading = false;
		}
	}

	async function confirmDeletePage() {
		if (!pageToDelete) return;
		deletePageConfirmOpen = false;
		try {
			const resp = await fetch(`/api/pages/${pageToDelete.id}`, { method: 'DELETE' });
			if (!resp.ok) throw new Error('Delete failed');
			toast.success(`Page ${pageToDelete.seq + 1} deleted.`);
			pageToDelete = null;
			await reload();
		} catch {
			toast.error('Could not delete page.');
		}
	}

	async function stitchPages(pg: PageData) {
		const idx = pages.findIndex((p) => p.id === pg.id);
		if (idx === -1 || idx >= pages.length - 1) return;
		const nextPg = pages[idx + 1];

		try {
			const resp = await fetch(`/api/chapters/${chapterId}/pages/stitch`, {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ topPageId: pg.id, bottomPageId: nextPg.id }),
			});
			if (!resp.ok) throw new Error('Stitch failed');
			toast.success(`Merged Page ${pg.seq + 1} and Page ${nextPg.seq + 1}.`);
			await reload();
		} catch {
			toast.error('Could not merge pages.');
		}
	}

	function handleMenuAction(action: string, pg: PageData) {
		if (action === 'translate') translateSinglePage(pg);
		else if (action === 'cancel') cancelSinglePage(pg);
		else if (action === 'inspect') openInspector(pg);
		else if (action === 'stitch') stitchPages(pg);
		else if (action === 'reset') clearPageProgress(pg);
		else if (action === 'delete') {
			pageToDelete = pg;
			deletePageConfirmOpen = true;
		}
	}

	function openInspector(pg: PageData) {
		inspectPage = pg;
		inspectModalOpen = true;
	}

	// DRAG & DROP EVENT HANDLERS
	function handleDragStart(e: DragEvent, idx: number) {
		draggedPageIndex = idx;
		if (e.dataTransfer) {
			e.dataTransfer.effectAllowed = 'move';
			e.dataTransfer.setData('text/plain', String(idx));
			e.dataTransfer.setData('application/x-manua-page-id', String(pages[idx].id));
		}
	}

	function handleDragOver(e: DragEvent, idx: number) {
		e.preventDefault();
		if (draggedPageIndex === null || draggedPageIndex === idx) return;
		dragOverPageIndex = idx;
		if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
	}

	async function handleDrop(e: DragEvent, targetIdx: number) {
		e.preventDefault();
		if (draggedPageIndex === null || draggedPageIndex === targetIdx) {
			handleDragEnd();
			return;
		}

		const fromIdx = draggedPageIndex;
		const reordered = [...pages];
		const [moved] = reordered.splice(fromIdx, 1);
		reordered.splice(targetIdx, 0, moved);
		pages = reordered.map((p, i) => ({ ...p, seq: i }));
		handleDragEnd();

		try {
			const pageIds = pages.map((p) => p.id);
			const resp = await fetch(`/api/chapters/${chapterId}/pages/reorder`, {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ pageIds }),
			});
			if (!resp.ok) throw new Error('Reorder failed');
			toast.success('Page order saved.');
		} catch {
			toast.error('Could not save page order.');
			await reload();
		}
	}

	function handleDragEnd() {
		draggedPageIndex = null;
		dragOverPageIndex = null;
	}

	// FILE DROP ON PAGE ROOT
	function handleRootDrop(e: DragEvent) {
		e.preventDefault();
		e.stopPropagation();
		isDraggingOver = false;
		if (draggedPageIndex !== null) return;
		if (e.dataTransfer?.files && e.dataTransfer.files.length > 0) {
			uploadFiles(e.dataTransfer.files);
		}
	}

	function handleRootDragOver(e: DragEvent) {
		if (draggedPageIndex !== null) return;
		if (!e.dataTransfer?.types?.includes('Files')) return;
		e.preventDefault();
		isDraggingOver = true;
	}
</script>

<!-- svelte-ignore a11y-no-static-element-interactions -->
<div
	class="flex flex-col gap-6"
	on:dragover={handleRootDragOver}
	on:dragleave={() => (isDraggingOver = false)}
	on:drop={handleRootDrop}
>
	<!-- DRAG OVERLAY -->
	{#if isDraggingOver}
		<div class="pointer-events-none fixed inset-0 z-50 flex items-center justify-center bg-[#b23a2e]/20 backdrop-blur-sm">
			<div class="flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-[#b23a2e] bg-white/90 p-8 shadow-2xl dark:bg-[#1a1713]/90">
				<Upload size={36} class="text-[#b23a2e] dark:text-[#e08a63] animate-bounce" />
				<p class="text-sm font-bold">Drop page images to add to Chapter {chapter ? chapter.seq + 1 : ''}</p>
			</div>
		</div>
	{/if}

	<!-- TOOLBAR -->
	<ChapterToolbar
		bookId={bookId ?? ''}
		{chapterId}
		chapterSeq={chapter?.seq ?? 0}
		chapterTitle={chapter?.title ?? null}
		chapterTitleTarget={chapter?.titleTarget ?? null}
		totalPages={pages.length}
		running={currentJobState.running}
		{uploading}
		{activeViewMode}
		{webtoonKind}
		{webtoonWidth}
		on:translate={() => startTranslation(false)}
		on:cancel={cancelTranslation}
		on:clearProgress={() => (clearChapterConfirmOpen = true)}
		on:openReslice={() => (resliceModalOpen = true)}
		on:upload={(e) => uploadFiles(e.detail)}
		on:changeViewMode={(e) => settings.update((s) => ({ ...s, readerViewMode: e.detail }))}
		on:changeWebtoonKind={(e) => settings.update((s) => ({ ...s, webtoonKind: e.detail }))}
		on:changeWebtoonWidth={(e) => settings.update((s) => ({ ...s, webtoonWidth: e.detail }))}
	/>

	<!-- REAL-TIME TELEMETRY PROGRESS TRACKER -->
	<PipelineProgressTracker
		jobState={currentJobState}
		onCancel={cancelTranslation}
		onRetryPage={(pageId) => {
			const pg = pages.find((p) => p.id === pageId);
			if (pg) translateSinglePage(pg);
		}}
	/>

	<!-- MAIN CONTENT VIEWS -->
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
		<ViewModeWebtoon
			pages={displayPages}
			running={currentJobState.running}
			{webtoonKind}
			{webtoonWidth}
			{reloadKey}
			{draggedPageIndex}
			{dragOverPageIndex}
			on:inspect={(e) => openInspector(e.detail)}
			on:menuAction={(e) => handleMenuAction(e.detail.action, e.detail.page)}
			on:dragStart={(e) => handleDragStart(e.detail.event, e.detail.index)}
			on:dragOver={(e) => handleDragOver(e.detail.event, e.detail.index)}
			on:drop={(e) => handleDrop(e.detail.event, e.detail.index)}
			on:dragEnd={handleDragEnd}
			on:toggleKind={() => settings.update((s) => ({ ...s, webtoonKind: s.webtoonKind === 'output' ? 'original' : 'output' }))}
		/>
	{:else if activeViewMode === 'grid'}
		<ViewModeGrid
			pages={displayPages}
			running={currentJobState.running}
			{reloadKey}
			{draggedPageIndex}
			{dragOverPageIndex}
			on:inspect={(e) => openInspector(e.detail)}
			on:menuAction={(e) => handleMenuAction(e.detail.action, e.detail.page)}
			on:dragStart={(e) => handleDragStart(e.detail.event, e.detail.index)}
			on:dragOver={(e) => handleDragOver(e.detail.event, e.detail.index)}
			on:drop={(e) => handleDrop(e.detail.event, e.detail.index)}
			on:dragEnd={handleDragEnd}
		/>
	{:else if activeViewMode === 'compare'}
		<ViewModeCompare
			pages={displayPages}
			running={currentJobState.running}
			{reloadKey}
			{draggedPageIndex}
			{dragOverPageIndex}
			on:inspect={(e) => openInspector(e.detail)}
			on:menuAction={(e) => handleMenuAction(e.detail.action, e.detail.page)}
			on:dragStart={(e) => handleDragStart(e.detail.event, e.detail.index)}
			on:dragOver={(e) => handleDragOver(e.detail.event, e.detail.index)}
			on:drop={(e) => handleDrop(e.detail.event, e.detail.index)}
			on:dragEnd={handleDragEnd}
		/>
	{/if}
</div>

<!-- PAGE REGION INSPECTOR MODAL -->
<PageInspectModal
	open={inspectModalOpen}
	page={inspectPage}
	{reloadKey}
	on:close={() => (inspectModalOpen = false)}
/>

<!-- RESLICE MODAL -->
<ResliceModal
	open={resliceModalOpen}
	{chapterId}
	pageCount={pages.length}
	on:close={() => (resliceModalOpen = false)}
	on:success={reload}
/>

<!-- CLEAR CHAPTER CONFIRMATION -->
<ConfirmDialog
	open={clearChapterConfirmOpen}
	title="Clear Chapter Progress?"
	message="This will reset all pages in this chapter back to 'pending', allowing a clean re-run."
	confirmLabel="Clear Progress"
	variant="danger"
	on:confirm={confirmClearChapterProgress}
	on:cancel={() => (clearChapterConfirmOpen = false)}
/>

<!-- DELETE PAGE CONFIRMATION -->
<ConfirmDialog
	open={deletePageConfirmOpen}
	title="Delete Page?"
	message={`Are you sure you want to delete Page ${pageToDelete ? pageToDelete.seq + 1 : ''}? This cannot be undone.`}
	confirmLabel="Delete Page"
	variant="danger"
	on:confirm={confirmDeletePage}
	on:cancel={() => (deletePageConfirmOpen = false)}
/>
